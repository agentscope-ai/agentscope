# -*- coding: utf-8 -*-
# pylint: disable=protected-access,too-many-instance-attributes
"""Router-level regression tests for the subagent HITL confirm loop (#2324).

Covers the two guards added in the fix:

- ``POST /chat`` rejects a confirmation whose worker is no longer parked
  on the ASKING tool call: the stale projected card is cleared, a
  ``subagent_user_confirm_result`` event is published, and the endpoint
  returns 409 so the front-end stops re-posting the same event.
- ``POST /chat`` keeps returning 200 ``started`` when the worker is still
  asking (existing behaviour preserved).
- ``POST /sessions/{id}/interrupt`` cascades to team members: their
  HITL-parked runs receive ``UserInterruptEvent`` resume triggers and the
  projected cards are purged + cleared.
"""

from typing import Any
from unittest import IsolatedAsyncioTestCase

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.app._router._chat import chat_router
from agentscope.app._router._session import session_router
from agentscope.app._service import SessionProjection, SubagentHitlProjector
from agentscope.app.deps import (
    get_chat_run_registry,
    get_chat_service,
    get_current_user_id,
    get_message_bus,
    get_storage,
)
from agentscope.app.message_bus import MessageBusKeys
from agentscope.app.storage._model._session import SessionRecord
from agentscope.app.storage._model._team import TeamRecord
from agentscope.event import ConfirmResult, UserConfirmResultEvent
from agentscope.message import AssistantMsg, TextBlock, ToolCallBlock
from agentscope.message._block import ToolCallState
from agentscope.state import AgentState

_LEADER_SID = "leader-sid"
_WORKER_SID = "worker-sid"
_LEADER_AGENT = "leader-agent"
_WORKER_AGENT = "worker-agent"
_REPLY_ID = "reply-1"
_TEAM_ID = "team-1"


class _FakeBus:
    """In-memory stand-in for the MessageBus surface used by
    ``SessionProjection``, ``publish_session_event`` and
    ``enqueue_run_trigger``."""

    def __init__(self) -> None:
        self.registry: dict[str, dict[str, str]] = {}
        self.logs: dict[str, list[tuple[str, dict]]] = {}
        self.queues: dict[str, list[dict]] = {}
        self._seq = 0

    async def registry_set(self, ns: str, field: str, value: str) -> None:
        """Store one registry field."""
        self.registry.setdefault(ns, {})[field] = value

    async def registry_del(self, ns: str, field: str) -> None:
        """Delete one registry field."""
        self.registry.get(ns, {}).pop(field, None)

    async def registry_getall(self, ns: str) -> dict[str, str]:
        """Return all fields in a namespace."""
        return dict(self.registry.get(ns, {}))

    async def registry_drop(self, ns: str) -> None:
        """Drop an entire namespace."""
        self.registry.pop(ns, None)

    async def log_append(
        self,
        key: str,
        event: dict,
        max_len: int | None = None,
    ) -> str:
        """Append to the replay log."""
        del max_len
        self._seq += 1
        entry_id = str(self._seq)
        self.logs.setdefault(key, []).append((entry_id, event))
        return entry_id

    async def publish(self, key: str, payload: dict) -> None:
        """Fan out live (no-op in this fake)."""
        del key, payload
        return None

    async def queue_push(self, key: str, payload: dict) -> None:
        """Push onto a work queue."""
        self.queues.setdefault(key, []).append(payload)


class _FakeStorage:
    """Returns pre-configured session / team records."""

    def __init__(
        self,
        session: SessionRecord,
        team: TeamRecord | None = None,
    ) -> None:
        self._session = session
        self._team = team

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return the pre-configured session."""
        del user_id, agent_id, session_id
        return self._session

    async def get_team(
        self,
        user_id: str,
        team_id: str,
    ) -> TeamRecord | None:
        """Return the pre-configured team."""
        del user_id, team_id
        return self._team


class _DummyChatService:
    """Minimal chat service surface for the interrupt endpoint."""

    async def interrupt(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> None:
        """No-op interrupt."""
        del user_id, session_id, agent_id
        return None


class _DummyRegistry:
    """Minimal chat-run registry (unused by the resume path)."""

    def spawn(self, coro: Any, *, session_id: str) -> None:  # pragma: no cover
        """No-op spawn."""
        del coro, session_id


def _session(state: AgentState, sid: str = _WORKER_SID) -> SessionRecord:
    """A worker session record carrying ``state``."""
    return SessionRecord.model_construct(
        id=sid,
        user_id="alice",
        agent_id=_WORKER_AGENT,
        team_id=_TEAM_ID,
        state=state,
    )


def _asking_session() -> SessionRecord:
    """Worker whose tail assistant message still has an ASKING tool call."""
    state = AgentState(session_id=_WORKER_SID)
    state.context.append(
        AssistantMsg(
            id=_REPLY_ID,
            name=_WORKER_AGENT,
            content=[
                ToolCallBlock(
                    id="tc-1",
                    name="Write",
                    input='{"file_path": "Memory/x.md"}',
                    state=ToolCallState.ASKING,
                ),
            ],
        ),
    )
    return _session(state)


def _idle_session() -> SessionRecord:
    """Worker whose tail assistant message has no pending tool call."""
    state = AgentState(session_id=_WORKER_SID)
    state.context.append(
        AssistantMsg(
            id=_REPLY_ID,
            name=_WORKER_AGENT,
            content=[TextBlock(text="all done")],
        ),
    )
    return _session(state)


def _leader_session() -> SessionRecord:
    """A leader session participating in ``_TEAM_ID``."""
    return SessionRecord.model_construct(
        id=_LEADER_SID,
        user_id="alice",
        agent_id=_LEADER_AGENT,
        team_id=_TEAM_ID,
    )


def _confirm_event() -> UserConfirmResultEvent:
    """The confirm result the front-end POSTs for ``_REPLY_ID``."""
    return UserConfirmResultEvent(
        reply_id=_REPLY_ID,
        confirm_results=[
            ConfirmResult(
                confirmed=True,
                tool_call=ToolCallBlock(
                    id="tc-1",
                    name="Write",
                    input='{"file_path": "Memory/x.md"}',
                    state=ToolCallState.ASKING,
                ),
            ),
        ],
    )


def _confirm_body() -> dict:
    """JSON body for the chat endpoint."""
    return {
        "agent_id": _LEADER_AGENT,
        "session_id": _LEADER_SID,
        "input": _confirm_event().model_dump(mode="json"),
    }


class ChatRouterHitlTest(IsolatedAsyncioTestCase):
    """Regression tests for the subagent HITL confirm loop."""

    async def asyncSetUp(self) -> None:
        """Fresh bus + projection per test."""
        self._bus = _FakeBus()
        self._projection = SessionProjection(self._bus)

    def _client(self, storage: _FakeStorage) -> TestClient:
        """Build a minimal app with both routers and fake deps."""
        app = FastAPI()
        app.include_router(chat_router)
        app.include_router(session_router)
        app.dependency_overrides[get_current_user_id] = lambda: "alice"
        app.dependency_overrides[get_storage] = lambda: storage
        app.dependency_overrides[get_message_bus] = lambda: self._bus
        app.dependency_overrides[get_chat_service] = _DummyChatService
        app.dependency_overrides[get_chat_run_registry] = _DummyRegistry
        return self.enterContext(TestClient(app))

    async def _seed_projection(self) -> None:
        """Project one pending worker card onto the leader session."""
        await self._projection.upsert(
            _LEADER_SID,
            SubagentHitlProjector.KIND,
            SubagentHitlProjector.entry_id(_WORKER_SID, _REPLY_ID),
            {
                "worker_session_id": _WORKER_SID,
                "worker_agent_id": _WORKER_AGENT,
                "reply_id": _REPLY_ID,
            },
        )

    async def test_confirm_rejected_when_worker_not_asking(self) -> None:
        """Stale confirm → 409, card cleared, clear event published."""
        storage = _FakeStorage(_idle_session())
        client = self._client(storage)
        await self._seed_projection()

        resp = client.post("/chat/", json=_confirm_body())
        self.assertEqual(resp.status_code, 409)

        entries = await self._projection.list(
            _LEADER_SID,
            SubagentHitlProjector.KIND,
        )
        self.assertEqual(entries, [])

        events = [
            event
            for _, event in self._bus.logs.get(
                MessageBusKeys.session_events(_LEADER_SID),
                [],
            )
            if event.get("name") == SubagentHitlProjector.EVT_RESULT
        ]
        self.assertEqual(len(events), 1)

    async def test_confirm_accepted_when_worker_still_asking(self) -> None:
        """Live confirm → 200 started, resume trigger enqueued."""
        storage = _FakeStorage(_asking_session())
        client = self._client(storage)
        await self._seed_projection()

        resp = client.post("/chat/", json=_confirm_body())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "started")

        triggers = self._bus.queues.get(MessageBusKeys.wakeup_queue(), [])
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["session_id"], _WORKER_SID)
        self.assertEqual(
            triggers[0]["kind"],
            MessageBusKeys.WAKEUP_KIND_RESUME,
        )

    async def test_interrupt_cascades_to_team_hitl_cards(self) -> None:
        """Leader interrupt purges projected cards and interrupts workers."""
        storage = _FakeStorage(
            _leader_session(),
            TeamRecord.model_construct(
                id=_TEAM_ID,
                user_id="alice",
                session_id=_LEADER_SID,
            ),
        )
        client = self._client(storage)
        await self._seed_projection()

        resp = client.post(
            f"/sessions/{_LEADER_SID}/interrupt",
            params={"agent_id": _LEADER_AGENT},
        )
        self.assertEqual(resp.status_code, 202)

        entries = await self._projection.list(
            _LEADER_SID,
            SubagentHitlProjector.KIND,
        )
        self.assertEqual(entries, [])

        triggers = list(
            self._bus.queues.get(
                MessageBusKeys.wakeup_queue(),
                [],
            ),
        )
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["session_id"], _WORKER_SID)
        self.assertEqual(
            triggers[0]["kind"],
            MessageBusKeys.WAKEUP_KIND_RESUME,
        )
        self.assertEqual(triggers[0]["input"]["reply_id"], _REPLY_ID)

        events = [
            event
            for _, event in self._bus.logs.get(
                MessageBusKeys.session_events(_LEADER_SID),
                [],
            )
            if event.get("name") == SubagentHitlProjector.EVT_RESULT
        ]
        self.assertEqual(len(events), 1)
