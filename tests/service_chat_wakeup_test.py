# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for wake-up driven ChatService runs."""
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ChatService
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
)
from agentscope.message import HintBlock


class _FakeStorage:
    """Minimal storage surface used by :class:`ChatService`."""

    def __init__(self) -> None:
        self.agent = AgentRecord(
            user_id="u",
            data=AgentData(
                name="A",
                system_prompt="You are A.",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        self.session = SessionRecord(
            user_id="u",
            agent_id=self.agent.data.id,
            config=SessionConfig(
                workspace_id="ws",
                chat_model_config=ChatModelConfig(
                    type="test",
                    credential_id="c",
                    model="m",
                    parameters={},
                ),
            ),
        )
        self.updated_states: list[Any] = []

    async def get_agent(self, user_id: str, agent_id: str) -> AgentRecord:
        """Return the single test agent."""
        assert user_id == "u"
        assert agent_id == self.agent.data.id
        return self.agent

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord:
        """Return the single test session."""
        assert user_id == "u"
        assert agent_id == self.agent.data.id
        assert session_id == "s"
        return self.session

    async def upsert_message(self, *_args: Any, **_kwargs: Any) -> None:
        """No-op message persistence for this focused regression."""

    async def get_message(self, *_args: Any, **_kwargs: Any) -> None:
        """No continuation message is needed in these tests."""
        return None

    async def update_session_state(
        self,
        *,
        state: Any,
        **_kwargs: Any,
    ) -> None:
        """Capture persisted states."""
        self.updated_states.append(state)


class _FakeWorkspace:
    """Workspace object with just the workdir ChatService records."""

    workdir = "/tmp/agentscope-test-workspace"


class _FakeWorkspaceManager:
    """Workspace manager returning one fake workspace."""

    async def get_workspace(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> _FakeWorkspace:
        """Return the fake workspace."""
        return _FakeWorkspace()


class _FakeBus:
    """In-memory bus covering session locks, events, and inbox drains."""

    def __init__(self) -> None:
        self.inbox: list[tuple[str, dict]] = []
        self.events: list[dict] = []

    @asynccontextmanager
    async def acquire_lock(
        self,
        _key: str,
        ttl_secs: int | None = None,
    ) -> AsyncGenerator[None, None]:
        """No-op lock context."""
        del ttl_secs
        yield

    async def queue_drain(
        self,
        _key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Drain queued test inbox entries."""
        entries = self.inbox[:max_count]
        self.inbox = self.inbox[max_count:]
        return entries

    async def log_append(
        self,
        _key: str,
        event: dict,
        max_len: int | None = None,
    ) -> str:
        """Capture published events."""
        del max_len
        self.events.append(event)
        return f"evt-{len(self.events)}"

    async def publish(self, _key: str, _payload: dict) -> None:
        """No-op live event fan-out."""

    async def log_trim(self, _key: str) -> None:
        """No-op replay-log trim."""


class _FakeAgent:
    """Agent double that records whether the model/reasoning path ran."""

    calls: list[Any] = []

    def __init__(
        self,
        *,
        name: str,
        state: Any,
        middlewares: list[Any],
        **_kwargs: Any,
    ) -> None:
        self.name = name
        self.state = state
        self.middlewares = middlewares

    async def reply_stream(
        self,
        inputs: Any = None,
    ) -> AsyncGenerator[Any, None]:
        """Record invocation and yield no model events."""
        self.calls.append(inputs)
        return
        yield  # pragma: no cover  # pylint: disable=unreachable


def _make_service(storage: _FakeStorage, bus: _FakeBus) -> ChatService:
    """Build a ChatService wired to local fakes."""
    return ChatService(
        storage=storage,
        workspace_manager=_FakeWorkspaceManager(),
        scheduler_manager=object(),
        background_task_manager=object(),
        message_bus=bus,
        custom_agent_cls=_FakeAgent,
    )


class TestChatServiceWakeupRuns(IsolatedAsyncioTestCase):
    """Wake-up runs should only invoke the agent when work was delivered."""

    async def asyncSetUp(self) -> None:
        """Reset class-level call tracking."""
        _FakeAgent.calls = []

    async def test_empty_wakeup_does_not_call_agent(self) -> None:
        """Duplicate wake-ups with an empty inbox are treated as no-ops."""
        storage = _FakeStorage()
        bus = _FakeBus()
        service = _make_service(storage, bus)

        with (
            patch(
                "agentscope.app._service._chat.get_model",
                AsyncMock(return_value=object()),
            ),
            patch(
                "agentscope.app._service._chat.get_toolkit",
                AsyncMock(return_value=object()),
            ),
        ):
            await service._run_impl("u", "s", storage.agent.data.id, None)

        self.assertEqual(_FakeAgent.calls, [])
        self.assertEqual(bus.events, [])
        self.assertEqual(storage.updated_states, [])

    async def test_wakeup_with_inbox_runs_agent_after_publishing_hints(
        self,
    ) -> None:
        """Pending inbox content is still delivered before agent reasoning."""
        storage = _FakeStorage()
        bus = _FakeBus()
        hint = HintBlock(hint="background result", source="tool")
        bus.inbox.append(("id-1", hint.model_dump(mode="json")))
        service = _make_service(storage, bus)

        with (
            patch(
                "agentscope.app._service._chat.get_model",
                AsyncMock(return_value=object()),
            ),
            patch(
                "agentscope.app._service._chat.get_toolkit",
                AsyncMock(return_value=object()),
            ),
        ):
            await service._run_impl("u", "s", storage.agent.data.id, None)

        self.assertEqual(_FakeAgent.calls, [None])
        self.assertEqual(len(bus.events), 1)
        self.assertEqual(bus.events[0]["type"], "HINT_BLOCK")
        self.assertEqual(bus.events[0]["block_id"], hint.id)
        self.assertEqual(len(storage.session.state.context), 1)
        self.assertEqual(storage.session.state.context[0].content[0], hint)
        self.assertEqual(storage.updated_states, [storage.session.state])
