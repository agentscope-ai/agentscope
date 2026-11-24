# -*- coding: utf-8 -*-
"""Projector that bridges team-member HITL events to leader sessions.

When a team *member* (worker) session hits a tool call that needs human
confirmation, the worker run parks on an ``ASKING`` tool call in its
**own** session — invisible to a client subscribed only to the *leader*
session's event stream. This projector mirrors each such pending
request onto the leader session so the leader UI can render and resolve
it, and clears it when the request is answered.

It is a thin strategy over the generic
:class:`~agentscope.app._service._session_projection.SessionProjection`
primitive: this file holds only the HITL-specific policy (which events
matter, how to resolve the leader, what the card payload looks like).
The durable hash, live notification, and key conventions all live in
the shared primitive.

Persistence model (see the design doc, §2.4):

- **Authoritative state** is the worker session's own ``state.context``
  (the ``ASKING`` tool call). The projection is only a mirror.
- The projected hash entry carries **no TTL**: a legitimate
  confirmation can stay pending indefinitely. Reconnecting readers check
  committed state. Only the worker updates the cards after its checkpoint.
"""
import json
from typing import TYPE_CHECKING, Sequence, TypeAlias

from ..._bus_ops import publish_session_event
from ...message_bus import MessageBusKeys
from ...storage._utils import _ensure_team_members
from ....message import ToolCallState
from ....event import (
    RequireUserConfirmEvent,
    RequireExternalExecutionEvent,
    UserConfirmResultEvent,
    ExternalExecutionResultEvent,
    ReplyEndEvent,
)

if TYPE_CHECKING:
    from ....message import ToolCallBlock
    from ...message_bus import MessageBus
    from ...storage import AgentRecord, SessionRecord, StorageBase
    from ....state import AgentState
    from ....event import AgentEvent
    from .._session_projection import SessionProjection


_Request: TypeAlias = RequireUserConfirmEvent | RequireExternalExecutionEvent
_OPEN_PENDING = "_open_notification_pending"


class SubagentHitlProjector:
    """Project pending team-member HITL requests onto leader sessions.

    Holds the storage handle it needs to resolve a worker's team (and
    thus its leader). The :class:`SessionProjection` it writes through
    is passed in per call by
    :class:`~agentscope.app._service.ChatService`, which also resolves
    confirm-routing and SSE replay through this projector's
    :meth:`resolve` / :meth:`entry_id` helpers.
    """

    KIND = "subagent_hitl"
    """Projection feed key (namespaces the entry within a session's
    shared projection hash)."""

    EVT_REQUIRE = "subagent_require_user_confirm"
    """``CustomEvent.name`` used to push/replay a pending request to the
    leader's event stream."""

    EVT_RESULT = "subagent_user_confirm_result"
    """``CustomEvent.name`` used to tell the leader UI a pending request
    has been resolved and its card should be cleared."""

    def __init__(self, storage: "StorageBase") -> None:
        """Bind the storage backend.

        Args:
            storage (`StorageBase`):
                Application storage, used to resolve the team (and hence
                the leader session) a worker session belongs to.
        """
        self._storage = storage

    @staticmethod
    def entry_id(
        worker_session_id: str,
        reply_id: str,
        request_id: str | None = None,
    ) -> str:
        """Address one request, including records predating request keys."""
        if request_id is None:
            return f"{worker_session_id}:{reply_id}"
        return json.dumps([worker_session_id, reply_id, request_id])

    @staticmethod
    def pending_card(
        card: dict,
        state: "AgentState",
        waiting: dict[str, "ToolCallBlock"] | None = None,
    ) -> dict | None:
        """Keep only calls still waiting for this particular request."""
        if state.reply_id != card["reply_id"]:
            return None
        if waiting is None:
            waiting = {c.id: c for c in state.get_awaiting_tool_calls(None)}
        event = card["event"]
        calls = []
        for payload in event["tool_calls"]:
            call = waiting.get(payload["id"])
            if call is None:
                continue
            if card["event_type"] == "require_user_confirm":
                if call.state != ToolCallState.ASKING:
                    continue
                current = state.confirmation_request(None, call.id)
                if current != event["id"] and (
                    current is not None or "request_id" in card
                ):
                    continue
            elif call.state != ToolCallState.SUBMITTED:
                continue
            calls.append(payload)
        if not calls:
            return None
        pending = {**card, "event": {**event, "tool_calls": calls}}
        pending.pop(_OPEN_PENDING, None)
        return pending

    @staticmethod
    def _request_id(state: "AgentState", call: "ToolCallBlock") -> str | None:
        """Resolve a pending call's ID, including older external requests."""
        if call.state == ToolCallState.ASKING:
            return state.confirmation_request(None, call.id)
        approval = state.reply_context.tool_approvals.get(call.id)
        return (
            approval.external_request_id if approval else None
        ) or f"external:{call.id}"

    @classmethod
    def pending_request_ids(cls, state: "AgentState") -> list[tuple[str, str]]:
        """Snapshot each pending card's identity before the worker resumes."""
        return list(
            dict.fromkeys(
                (state.reply_id, request_id)
                for call in state.get_awaiting_tool_calls(None)
                if (request_id := cls._request_id(state, call)) is not None
            ),
        )

    @classmethod
    def pending_requests(
        cls,
        state: "AgentState",
        covered: set[str] | None = None,
        *,
        waiting: list["ToolCallBlock"] | None = None,
    ) -> list[_Request]:
        """Rebuild request groups from the member's committed reply."""
        requests = {}
        if waiting is None:
            waiting = state.get_awaiting_tool_calls(None)
        for call in waiting:
            if covered and call.id in covered:
                continue
            request_id = cls._request_id(state, call)
            if request_id is None:
                continue
            if request_id not in requests:
                event_type = (
                    RequireUserConfirmEvent
                    if call.state == ToolCallState.ASKING
                    else RequireExternalExecutionEvent
                )
                requests[request_id] = event_type(
                    id=request_id,
                    reply_id=state.reply_id,
                    created_at=call.created_at,
                    tool_calls=[],
                )
            requests[request_id].tool_calls.append(call.model_copy(deep=True))
        return list(requests.values())

    @staticmethod
    def _card(
        worker_sid: str,
        agent_id: str,
        name: str,
        event: _Request,
    ) -> dict:
        """Serialize a request for the leader UI."""
        return {
            "worker_session_id": worker_sid,
            "worker_agent_id": agent_id,
            "worker_agent_name": name,
            "reply_id": event.reply_id,
            "request_id": event.id,
            "event_type": event.type.lower(),
            "event": event.model_dump(mode="json"),
            "created_at": event.created_at,
        }

    @classmethod
    def _pending_cards(
        cls,
        worker_sid: str,
        agent_id: str,
        state: "AgentState",
        projected: list[dict],
        waiting: list["ToolCallBlock"],
        *,
        recover: bool = True,
    ) -> list[dict]:
        """Merge the projected payloads with any missing pending calls."""
        by_id = {call.id: call for call in waiting}
        cards = [
            pending
            for card in projected
            if (pending := cls.pending_card(card, state, by_id))
        ]
        if recover and waiting:
            reply = state.get_reply_message(None)
            covered = {
                call["id"]
                for card in cards
                for call in card["event"]["tool_calls"]
            }
            for event in cls.pending_requests(state, covered, waiting=waiting):
                cards.append(
                    cls._card(worker_sid, agent_id, reply.name, event),
                )
        return cards

    async def _reissue_missing(
        self,
        user_id: str,
        worker: "SessionRecord",
        bus: "MessageBus",
    ) -> "SessionRecord | None":
        """Save new prompt IDs before exposing them to either session."""
        sid = worker.id
        async with bus.acquire_lock(MessageBusKeys.session_lock(sid)):
            current = await self._storage.get_session(
                user_id,
                worker.agent_id,
                sid,
            )
            if current is None or current.team_id != worker.team_id:
                return None
            reply = current.state.get_reply_message(None)
            if reply is None:
                return current
            prompts = RequireUserConfirmEvent.reissue_missing(reply)
            if not prompts:
                return current
            async with bus.acquire_lock(
                MessageBusKeys.session_event_checkpoint_lock(sid),
            ):
                history = await self._storage.get_message(
                    user_id,
                    sid,
                    reply.id,
                )
                if history is not None:
                    for prompt in prompts:
                        history.append_event(prompt)
                await self._storage.upsert_message(
                    user_id,
                    sid,
                    history or reply,
                )
                await self._storage.update_session_state(
                    user_id=user_id,
                    agent_id=worker.agent_id,
                    session_id=sid,
                    state=current.state,
                )
                for prompt in prompts:
                    await publish_session_event(
                        bus,
                        sid,
                        prompt.model_dump(mode="json"),
                    )
            return current

    async def snapshot(
        self,
        user_id: str,
        leader: "SessionRecord",
        projected: list[dict],
        bus: "MessageBus",
    ) -> list[dict]:
        """Read pending cards, including workers missing from the mirror."""
        workers = {}
        by_worker: dict[str, list[dict]] = {}
        for card in projected:
            sid = card["worker_session_id"]
            workers[sid] = card["worker_agent_id"]
            by_worker.setdefault(sid, []).append(card)
        from_roster = False
        if leader.team_id:
            team = await self._storage.get_team(user_id, leader.team_id)
            if team and team.session_id == leader.id:
                members = await _ensure_team_members(
                    self._storage,
                    user_id,
                    team,
                )
                workers = {m.session_id: m.agent_id for m in members}
                from_roster = True
        cards = []
        for sid, agent_id in workers.items():
            worker = await self._storage.get_session(user_id, agent_id, sid)
            if worker is None or (
                from_roster and worker.team_id != leader.team_id
            ):
                continue
            waiting = worker.state.get_awaiting_tool_calls(None)
            if from_roster and any(
                call.state == ToolCallState.ASKING
                and worker.state.confirmation_request(None, call.id) is None
                for call in waiting
            ):
                worker = await self._reissue_missing(user_id, worker, bus)
                if worker is None:
                    continue
                waiting = worker.state.get_awaiting_tool_calls(None)
            cards.extend(
                self._pending_cards(
                    sid,
                    agent_id,
                    worker.state,
                    by_worker.get(sid, []),
                    waiting,
                    recover=from_roster,
                ),
            )
        return cards

    async def sync(
        self,
        leader_sid: str,
        worker_sid: str,
        agent: "AgentRecord",
        state: "AgentState",
        requests: list[_Request],
        projection: "SessionProjection",
        previous_request_ids: Sequence[tuple[str, str]] = (),
    ) -> None:
        """Publish the worker's committed changes under its session lock."""
        previous = {
            (card["reply_id"], card["event"]["id"]): card
            for card in await projection.list(leader_sid, self.KIND)
            if card["worker_session_id"] == worker_sid
        }
        candidates = previous.copy()
        for event in requests:
            candidates.setdefault(
                (event.reply_id, event.id),
                self._card(worker_sid, agent.id, agent.data.name, event),
            )
        pending = self._pending_cards(
            worker_sid,
            agent.id,
            state,
            list(candidates.values()),
            state.get_awaiting_tool_calls(None),
        )
        closed = {
            key: card.get("request_id") for key, card in previous.items()
        }
        # A reconnect may have recovered a card whose first write failed.
        for reply_id, request_id in previous_request_ids:
            closed.setdefault((reply_id, request_id), request_id)
        for card in pending:
            card_id = (card["reply_id"], card["event"]["id"])
            closed.pop(card_id, None)
            stored = previous.get(card_id)
            if stored != card:
                key = self.entry_id(
                    worker_sid,
                    card["reply_id"],
                    card.get("request_id"),
                )
                # Persist the route and retry marker before notifying clients.
                queued = {**card, _OPEN_PENDING: True}
                if stored != queued:
                    await projection.upsert(leader_sid, self.KIND, key, queued)
                await projection.publish(leader_sid, self.EVT_REQUIRE, card)
                await projection.upsert(leader_sid, self.KIND, key, card)

        for (reply_id, request_id), stored_id in closed.items():
            key = self.entry_id(worker_sid, reply_id, stored_id)
            # A stored card lets the next sync retry an undelivered close.
            await projection.publish(
                leader_sid,
                self.EVT_RESULT,
                {
                    "worker_session_id": worker_sid,
                    "reply_id": reply_id,
                    "request_id": request_id,
                },
            )
            await projection.delete(leader_sid, self.KIND, key)

    async def maybe_project(
        self,
        user_id: str,
        session_record: "SessionRecord",
        agent_record: "AgentRecord",
        event: "AgentEvent",
        projection: "SessionProjection",
    ) -> None:
        """Project one HITL update from a committed session record."""
        if not session_record.team_id or not isinstance(
            event,
            (
                RequireUserConfirmEvent,
                RequireExternalExecutionEvent,
                UserConfirmResultEvent,
                ExternalExecutionResultEvent,
                ReplyEndEvent,
            ),
        ):
            return
        team = await self._storage.get_team(user_id, session_record.team_id)
        if team is not None and team.session_id != session_record.id:
            await self.sync(
                team.session_id,
                session_record.id,
                agent_record,
                session_record.state,
                [event]
                if isinstance(
                    event,
                    (RequireUserConfirmEvent, RequireExternalExecutionEvent),
                )
                else [],
                projection,
            )

    @classmethod
    async def resolve(
        cls,
        projection: "SessionProjection",
        leader_sid: str,
        reply_id: str,
        receipt: UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> dict | None:
        """Find the pending entry for ``reply_id`` under a leader.

        Used by the confirm-routing entry point (the chat router): given
        a confirm result POSTed to the leader session, locate which
        worker session it actually belongs to so the result can be
        forwarded there.

        Args:
            projection (`SessionProjection`):
                The shared projection store to scan.
            leader_sid (`str`):
                The leader session the confirm result was POSTed to.
            reply_id (`str`):
                The worker-side reply id carried by the confirm result.
            receipt:
                Match its prompt and tool-call IDs when routing a reply.

        Returns:
            `dict | None`:
                The stored payload (with ``worker_session_id`` /
                ``worker_agent_id``), or ``None`` when no pending entry
                matches — meaning the confirm is the leader's own.
        """
        return cls.match(
            await projection.list(leader_sid, cls.KIND),
            reply_id,
            receipt,
        )

    @staticmethod
    def match(
        cards: list[dict],
        reply_id: str,
        receipt: UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> dict | None:
        """Find the card addressed by a receipt."""
        for entry in cards:
            if entry["reply_id"] != reply_id:
                continue
            if receipt is None:
                return entry
            event = entry["event"]
            call_ids = {call["id"] for call in event["tool_calls"]}
            if isinstance(receipt, UserConfirmResultEvent):
                if entry["event_type"] == "require_user_confirm" and any(
                    r.tool_call.id in call_ids
                    and r.request_id in (event["id"], None)
                    for r in receipt.confirm_results
                ):
                    return entry
            elif entry["event_type"] == "require_external_execution" and any(
                result.id in call_ids for result in receipt.execution_results
            ):
                return entry
        return None

    @classmethod
    async def purge(
        cls,
        projection: "SessionProjection",
        leader_sid: str,
    ) -> None:
        """Drop every pending HITL entry for a leader session.

        Used when the leader session (or its team) is deleted. Scoped to
        this feed so other projections on the same session survive.

        Args:
            projection (`SessionProjection`):
                The shared projection store.
            leader_sid (`str`):
                The leader session to purge.
        """
        await projection.purge(leader_sid, cls.KIND)

    @classmethod
    async def drop_worker(
        cls,
        projection: "SessionProjection",
        leader_sid: str,
        worker_sid: str,
    ) -> None:
        """Drop every pending entry that originated from one worker.

        Used when a single worker session is deleted while the leader
        survives.

        Args:
            projection (`SessionProjection`):
                The shared projection store.
            leader_sid (`str`):
                The leader session the entries are projected onto.
            worker_sid (`str`):
                The worker session whose entries should be dropped.
        """
        for entry in await projection.list(leader_sid, cls.KIND):
            if entry.get("worker_session_id") == worker_sid:
                await projection.delete(
                    leader_sid,
                    cls.KIND,
                    cls.entry_id(
                        worker_sid,
                        entry["reply_id"],
                        entry.get("request_id"),
                    ),
                )
