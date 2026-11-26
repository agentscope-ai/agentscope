# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :class:`SubagentHitlProjector` — the strategy that mirrors a
team *member* (worker) session's HITL request onto its *leader* session.

Covers the projection policy:

- A worker ``Require*`` event upserts a pending card on the leader and
  publishes the live ``EVT_REQUIRE`` notification.
- A worker ``*Result`` / ``ReplyEnd`` event deletes the card and
  publishes the ``EVT_RESULT`` clear notification.
- No-op for non-team agents, for sessions with no ``team_id``, and for
  the leader session itself (its own HITL reaches its client directly).
- ``resolve`` finds the worker entry behind a leader+reply_id, and
  returns ``None`` when none matches.
"""
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app._service import SubagentHitlProjector
from agentscope.app.storage._model._agent import AgentRecord, AgentData
from agentscope.app.storage._model._session import SessionRecord
from agentscope.app.storage._model._team import TeamRecord
from agentscope.message import ToolCallBlock, ToolCallState
from agentscope.state import AgentState
from agentscope.event import (
    RequireUserConfirmEvent,
    RequireExternalExecutionEvent,
    UserConfirmResultEvent,
    ExternalExecutionResultEvent,
    ReplyEndEvent,
)


class _FakeProjection:
    """Records every mutation the projector makes, no Redis required.

    Mimics :class:`SessionProjection`'s surface (``upsert`` / ``delete``
    / ``publish`` / ``list``) over an in-memory ``{(sid, kind): {eid:
    payload}}`` store so tests can assert exactly what was projected.
    """

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict[str, dict]] = {}
        self.published: list[tuple[str, str, dict]] = []

    async def upsert(
        self,
        target_sid: str,
        kind: str,
        entry_id: str,
        payload: dict,
    ) -> None:
        """Record an upsert in the in-memory store."""
        self.store.setdefault((target_sid, kind), {})[entry_id] = payload

    async def delete(
        self,
        target_sid: str,
        kind: str,
        entry_id: str,
    ) -> None:
        """Remove an entry from the in-memory store."""
        self.store.get((target_sid, kind), {}).pop(entry_id, None)

    async def list(self, target_sid: str, kind: str) -> list[dict]:
        """Return all entries for the given session and kind."""
        return list(self.store.get((target_sid, kind), {}).values())

    async def publish(
        self,
        target_sid: str,
        event_name: str,
        value: dict,
    ) -> None:
        """Record a published event for later assertion."""
        self.published.append((target_sid, event_name, value))


class _FakeStorage:
    """Returns a fixed team for ``get_team``; ``None`` to simulate a
    missing / non-team lookup."""

    def __init__(self, team: TeamRecord | None) -> None:
        self._team = team

    async def get_team(
        self,
        _user_id: str,
        _team_id: str,
    ) -> TeamRecord | None:
        """Return the pre-configured team record."""
        return self._team


_LEADER_SID = "leader-sid"
_WORKER_SID = "worker-sid"
_TEAM_ID = "team-1"


def _team() -> TeamRecord:
    """A team whose leader is ``_LEADER_SID``."""
    return TeamRecord.model_construct(
        id=_TEAM_ID,
        user_id="u",
        session_id=_LEADER_SID,
    )


def _session(sid: str, team_id: str | None = _TEAM_ID) -> SessionRecord:
    """A session record with just the fields the projector reads."""
    return SessionRecord.model_construct(
        id=sid,
        user_id="u",
        agent_id="wa1",
        team_id=team_id,
        state=AgentState(),
    )


def _agent(source: str = "team") -> AgentRecord:
    """An agent record with just the fields the projector reads."""
    return AgentRecord.model_construct(
        id="wa1",
        user_id="u",
        source=source,
        data=AgentData.model_construct(name="researcher"),
    )


def _projector(team: TeamRecord | None = None) -> SubagentHitlProjector:
    return SubagentHitlProjector(_FakeStorage(team if team else _team()))


def _entry_id() -> str:
    return SubagentHitlProjector.entry_id(_WORKER_SID, "r1", "request")


def _park(session: SessionRecord, event: Any) -> None:
    """Record the prompt as it would be committed by the member."""
    event.id = "request"
    call = ToolCallBlock(
        id="call",
        name="inspect",
        input="{}",
        state="asking"
        if isinstance(event, RequireUserConfirmEvent)
        else "submitted",
    )
    event.tool_calls = [call]
    session.state.reply_id = event.reply_id
    session.state.append_context("researcher", [call])
    if isinstance(event, RequireUserConfirmEvent):
        session.state.context[-1].set_confirmation_request(call.id, event.id)


class TestSubagentHitlProjectorRequire(IsolatedAsyncioTestCase):
    """The require → upsert + publish path."""

    async def test_failed_open_publish_reconciles_from_stored_card(
        self,
    ) -> None:
        """Retry a missed open only if the worker is still waiting."""
        for resolved in (False, True):
            with self.subTest(resolved=resolved):
                store = _FakeProjection()
                worker = _session(_WORKER_SID)
                args = (_LEADER_SID, _WORKER_SID, _agent(), worker.state)
                projector = _projector()
                _park(
                    worker,
                    RequireUserConfirmEvent(reply_id="old", tool_calls=[]),
                )
                await projector.sync(*args, [], store)
                event = RequireUserConfirmEvent(reply_id="new", tool_calls=[])
                _park(worker, event)
                card = projector._card(_WORKER_SID, "wa1", "researcher", event)
                with patch.object(
                    store,
                    "publish",
                    side_effect=OSError("offline"),
                ):
                    with self.assertRaises(OSError):
                        await projector.sync(*args, [event], store)
                _, saved = await store.list(_LEADER_SID, projector.KIND)
                self.assertEqual(
                    projector.pending_card(saved, worker.state),
                    card,
                )
                store.published.clear()
                if resolved:
                    (call,) = worker.state.get_awaiting_tool_calls(None)
                    call.state = ToolCallState.FINISHED
                await _projector().sync(*args, [], store)
                await _projector().sync(*args, [], store)
                opened = [
                    payload
                    for _, name, payload in store.published
                    if name == projector.EVT_REQUIRE
                ]
                closed = [
                    payload["reply_id"]
                    for _, name, payload in store.published
                    if name == projector.EVT_RESULT
                ]
                self.assertEqual(opened, [] if resolved else [card])
                self.assertCountEqual(
                    closed,
                    ["old", "new"] if resolved else ["old"],
                )
                self.assertEqual(
                    await store.list(_LEADER_SID, projector.KIND),
                    opened,
                )

    async def _run_require(self, event: Any) -> _FakeProjection:
        projection = _FakeProjection()
        session = _session(_WORKER_SID)
        _park(session, event)
        await _projector().maybe_project(
            "u",
            session,
            _agent(),
            event,
            projection,
        )
        return projection

    async def test_require_user_confirm_upserts_and_publishes(self) -> None:
        """A worker ``RequireUserConfirmEvent`` writes a leader card and
        fires the live require notification."""
        event = RequireUserConfirmEvent.model_construct(
            reply_id="r1",
            tool_calls=[],
        )
        projection = await self._run_require(event)

        card = projection.store[(_LEADER_SID, SubagentHitlProjector.KIND)]
        self.assertIn(_entry_id(), card)
        payload = card[_entry_id()]
        self.assertEqual(payload["worker_session_id"], _WORKER_SID)
        self.assertEqual(payload["reply_id"], "r1")
        self.assertEqual(payload["event_type"], "require_user_confirm")

        self.assertEqual(len(projection.published), 1)
        sid, name, _ = projection.published[0]
        self.assertEqual(sid, _LEADER_SID)
        self.assertEqual(name, SubagentHitlProjector.EVT_REQUIRE)

    async def test_require_external_execution_marks_event_type(self) -> None:
        """A ``RequireExternalExecutionEvent`` carries the matching
        ``event_type`` discriminator."""
        event = RequireExternalExecutionEvent.model_construct(
            reply_id="r1",
            tool_calls=[],
        )
        projection = await self._run_require(event)

        card = projection.store[(_LEADER_SID, SubagentHitlProjector.KIND)]
        self.assertEqual(
            card[_entry_id()]["event_type"],
            "require_external_execution",
        )

    async def test_invited_agent_source_user_still_projects(self) -> None:
        """An ``AgentInvite``-borrowed session runs on a
        ``source='user'`` agent record but is still a team worker — its
        HITL request must project onto the leader like any other
        team-member session."""
        projection = _FakeProjection()
        session = _session(_WORKER_SID)
        event = RequireUserConfirmEvent(reply_id="r1", tool_calls=[])
        _park(session, event)
        await _projector().maybe_project(
            "u",
            session,
            _agent(source="user"),
            event,
            projection,
        )
        card = projection.store[(_LEADER_SID, SubagentHitlProjector.KIND)]
        self.assertIn(_entry_id(), card)


class TestSubagentHitlProjectorClear(IsolatedAsyncioTestCase):
    """The result / reply-end → delete + publish path."""

    async def test_retry_clears_only_old_reply_with_same_fallback_id(
        self,
    ) -> None:
        """Retry the old delete after the replacement card was written."""
        store = _FakeProjection()
        projector = _projector()
        worker = _session(_WORKER_SID)

        async def sync() -> None:
            await projector.sync(
                _LEADER_SID,
                _WORKER_SID,
                _agent(),
                worker.state,
                [],
                store,
            )

        _park(
            worker,
            RequireExternalExecutionEvent(reply_id="old", tool_calls=[]),
        )
        await sync()
        _park(
            worker,
            RequireExternalExecutionEvent(reply_id="new", tool_calls=[]),
        )
        with patch.object(store, "delete", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                await sync()
        old, new = await store.list(_LEADER_SID, projector.KIND)
        self.assertEqual((old["reply_id"], new["reply_id"]), ("old", "new"))
        self.assertEqual(old["request_id"], new["request_id"])
        close = (
            _LEADER_SID,
            projector.EVT_RESULT,
            {
                "worker_session_id": _WORKER_SID,
                "reply_id": "old",
                "request_id": "external:call",
            },
        )
        self.assertEqual(store.published[-1], close)
        store.published.clear()
        await sync()
        await sync()
        self.assertEqual(await store.list(_LEADER_SID, projector.KIND), [new])
        self.assertEqual(store.published, [close])

    async def test_failed_close_publish_keeps_card_for_retry(self) -> None:
        """A new sync can resend the close using only persisted records."""
        projector = _projector()
        store = _FakeProjection()
        worker = _session(_WORKER_SID)
        _park(worker, RequireUserConfirmEvent(reply_id="r1", tool_calls=[]))

        async def sync() -> None:
            await projector.sync(
                _LEADER_SID,
                _WORKER_SID,
                _agent(),
                worker.state,
                [],
                store,
            )

        await sync()
        cards = await store.list(_LEADER_SID, projector.KIND)
        self.assertEqual(len(cards), 1)
        store.published.clear()
        worker.state = AgentState()
        with patch.object(store, "publish", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                await sync()
        self.assertEqual(await store.list(_LEADER_SID, projector.KIND), cards)
        await sync()
        await sync()
        self.assertEqual(await store.list(_LEADER_SID, projector.KIND), [])
        self.assertEqual(
            store.published,
            [
                (
                    _LEADER_SID,
                    projector.EVT_RESULT,
                    {
                        "worker_session_id": _WORKER_SID,
                        "reply_id": "r1",
                        "request_id": "request",
                    },
                ),
            ],
        )

    async def _seed_then(self, clear_event: Any) -> _FakeProjection:
        projection = _FakeProjection()
        projector = _projector()
        # Seed a pending card first.
        session = _session(_WORKER_SID)
        event = RequireUserConfirmEvent(reply_id="r1", tool_calls=[])
        _park(session, event)
        await projector.maybe_project(
            "u",
            session,
            _agent(),
            event,
            projection,
        )
        projection.published.clear()
        # Now clear it.
        await projector.maybe_project(
            "u",
            _session(_WORKER_SID),
            _agent(),
            clear_event,
            projection,
        )
        return projection

    async def test_user_confirm_result_clears_card(self) -> None:
        """A worker ``UserConfirmResultEvent`` deletes the leader card and
        publishes the clear notification."""
        projection = await self._seed_then(
            UserConfirmResultEvent.model_construct(
                reply_id="r1",
                confirm_results=[],
            ),
        )
        card = projection.store[(_LEADER_SID, SubagentHitlProjector.KIND)]
        self.assertNotIn(_entry_id(), card)

        self.assertEqual(len(projection.published), 1)
        sid, name, value = projection.published[0]
        self.assertEqual(sid, _LEADER_SID)
        self.assertEqual(name, SubagentHitlProjector.EVT_RESULT)
        self.assertEqual(value["worker_session_id"], _WORKER_SID)
        self.assertEqual(value["reply_id"], "r1")

    async def test_external_execution_result_clears_card(self) -> None:
        """An ``ExternalExecutionResultEvent`` also clears the card."""
        projection = await self._seed_then(
            ExternalExecutionResultEvent.model_construct(
                reply_id="r1",
                execution_results=[],
            ),
        )
        card = projection.store[(_LEADER_SID, SubagentHitlProjector.KIND)]
        self.assertNotIn(_entry_id(), card)
        self.assertEqual(
            projection.published[0][1],
            SubagentHitlProjector.EVT_RESULT,
        )

    async def test_partial_request_keeps_unanswered_sibling(self) -> None:
        """Checkpoint updates and replay agree on a partly answered card."""
        session = _session(_WORKER_SID)
        event = RequireUserConfirmEvent(reply_id="r1", tool_calls=[])
        _park(session, event)
        first = event.tool_calls[0]
        second = first.model_copy(update={"id": "second"})
        reply = session.state.context[-1]
        reply.content.append(second)
        reply.set_confirmation_request(second.id, event.id)
        event.tool_calls.append(second)
        store = _FakeProjection()
        projector = _projector()
        await projector.sync(
            _LEADER_SID,
            _WORKER_SID,
            _agent(),
            session.state,
            [event],
            store,
        )
        previous = store.published[-1][2]
        with patch.object(
            ToolCallBlock,
            "model_copy",
            side_effect=AssertionError,
        ):
            request_ids = projector.pending_request_ids(session.state)
        self.assertEqual(request_ids, [("r1", event.id)])
        reply.get_content_blocks("tool_call")[0].state = ToolCallState.FINISHED
        expected = {
            **previous,
            "event": {
                **previous["event"],
                "tool_calls": [second.model_dump(mode="json")],
            },
        }
        self.assertEqual(
            projector.pending_card(previous, session.state),
            expected,
        )
        await projector.sync(
            _LEADER_SID,
            _WORKER_SID,
            _agent(),
            session.state,
            [],
            store,
            request_ids,
        )
        self.assertEqual(
            await store.list(_LEADER_SID, projector.KIND),
            [expected],
        )
        self.assertEqual(
            store.published[-1],
            (_LEADER_SID, projector.EVT_REQUIRE, expected),
        )
        # A reconnect can show this card even if its write never succeeded.
        store.store.clear()
        store.published.clear()
        second.state = ToolCallState.FINISHED
        with patch.object(projector, "_card", side_effect=AssertionError):
            await projector.sync(
                _LEADER_SID,
                _WORKER_SID,
                _agent(),
                session.state,
                [],
                store,
                request_ids,
            )
        self.assertEqual(
            store.published,
            [
                (
                    _LEADER_SID,
                    projector.EVT_RESULT,
                    {
                        "worker_session_id": _WORKER_SID,
                        "reply_id": "r1",
                        "request_id": event.id,
                    },
                ),
            ],
        )

    async def test_reply_end_clears_card(self) -> None:
        """``ReplyEndEvent`` is the primary clear signal (the resume's
        continuation events are not republished through the stream)."""
        projection = await self._seed_then(
            ReplyEndEvent.model_construct(reply_id="r1"),
        )
        card = projection.store[(_LEADER_SID, SubagentHitlProjector.KIND)]
        self.assertNotIn(_entry_id(), card)
        self.assertEqual(
            projection.published[0][1],
            SubagentHitlProjector.EVT_RESULT,
        )

    async def test_snapshot_closes_legacy_external_card(self) -> None:
        """Replay and cleanup agree on fallback IDs and old storage keys."""
        session = _session(_WORKER_SID)
        _park(
            session,
            RequireExternalExecutionEvent(reply_id="r1", tool_calls=[]),
        )
        state = session.state
        reply = state.context[-1]
        call = reply.get_content_blocks("tool_call")[0]
        reply.content.append(
            call.model_copy(
                update={
                    "id": "unissued",
                    "state": ToolCallState.ASKING,
                },
            ),
        )
        projector = _projector()
        ids = projector.pending_request_ids(state)
        self.assertEqual(ids, [("r1", "external:call")])
        store = _FakeProjection()
        await projector.sync(
            _LEADER_SID,
            _WORKER_SID,
            _agent(),
            state,
            [],
            store,
        )
        (card,) = await store.list(_LEADER_SID, projector.KIND)
        self.assertEqual(card.pop("request_id"), "external:call")
        store.store.clear()
        await store.upsert(
            _LEADER_SID,
            projector.KIND,
            projector.entry_id(_WORKER_SID, "r1"),
            card,
        )
        store.published.clear()
        call.state = ToolCallState.FINISHED
        await projector.sync(
            _LEADER_SID,
            _WORKER_SID,
            _agent(),
            state,
            [],
            store,
            ids,
        )
        self.assertEqual(await store.list(_LEADER_SID, projector.KIND), [])
        self.assertEqual(
            store.published,
            [
                (
                    _LEADER_SID,
                    projector.EVT_RESULT,
                    {
                        "worker_session_id": _WORKER_SID,
                        "reply_id": "r1",
                        "request_id": "external:call",
                    },
                ),
            ],
        )


class TestSubagentHitlProjectorNoOp(IsolatedAsyncioTestCase):
    """Cases where nothing should be projected."""

    async def _assert_noop(
        self,
        *,
        agent_source: str = "team",
        team_id: str | None = _TEAM_ID,
        team: TeamRecord | None = None,
        session_id: str = _WORKER_SID,
    ) -> None:
        projection = _FakeProjection()
        projector = SubagentHitlProjector(
            _FakeStorage(team if team is not None else _team()),
        )
        await projector.maybe_project(
            "u",
            _session(session_id, team_id=team_id),
            _agent(source=agent_source),
            RequireUserConfirmEvent.model_construct(
                reply_id="r1",
                tool_calls=[],
            ),
            projection,
        )
        self.assertEqual(projection.store, {})
        self.assertEqual(projection.published, [])

    async def test_session_without_team_id_is_noop(self) -> None:
        """A session with no ``team_id`` never projects."""
        await self._assert_noop(team_id=None)

    async def test_leader_session_is_noop(self) -> None:
        """The leader's own HITL reaches its client directly — no
        self-projection."""
        await self._assert_noop(session_id=_LEADER_SID)

    async def test_unrelated_event_is_noop(self) -> None:
        """Events outside the HITL set are ignored."""
        projection = _FakeProjection()
        await _projector().maybe_project(
            "u",
            _session(_WORKER_SID),
            _agent(),
            ReplyEndEvent.model_construct(reply_id="r1"),
            projection,
        )
        # ReplyEnd with no seeded card: delete is a no-op, but it DOES
        # publish a (harmless, idempotent) clear. Assert no card written.
        self.assertEqual(projection.store, {})


class TestSubagentHitlProjectorResolve(IsolatedAsyncioTestCase):
    """The router-side ``resolve`` helper."""

    async def test_resolve_finds_and_misses(self) -> None:
        """``resolve`` returns the worker entry for a known reply_id and
        ``None`` for an unknown one."""
        projection = _FakeProjection()
        session = _session(_WORKER_SID)
        event = RequireUserConfirmEvent(reply_id="r1", tool_calls=[])
        _park(session, event)
        await _projector().maybe_project(
            "u",
            session,
            _agent(),
            event,
            projection,
        )

        hit = await SubagentHitlProjector.resolve(
            projection,
            _LEADER_SID,
            "r1",
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["worker_session_id"], _WORKER_SID)

        miss = await SubagentHitlProjector.resolve(
            projection,
            _LEADER_SID,
            "nope",
        )
        self.assertIsNone(miss)
