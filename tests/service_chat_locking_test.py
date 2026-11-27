# -*- coding: utf-8 -*-
# pylint: disable=protected-access, using-constant-test
"""Concurrency regression tests for :class:`ChatService` session loading."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ChatService
from agentscope.app._manager import CancelDispatcher, ChatRunRegistry
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
)
from agentscope.message import Msg, TextBlock, UserMsg
from agentscope.event import ReplyStartEvent, ReplyEndEvent
from agentscope.permission import AdditionalWorkingDirectory
from agentscope.state import AgentState


class _ContendedBus(InMemoryMessageBus):
    """Expose when the second run starts waiting for the session lock."""

    def __init__(self) -> None:
        super().__init__()
        self._test_lock = asyncio.Lock()
        self.acquire_attempts = 0
        self.second_attempted = asyncio.Event()

    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        """Serialize callers and signal immediately before caller two waits."""
        if key != MessageBusKeys.session_lock("session-1"):
            # Locks on other keys (e.g. the inbox lock taken while the
            # session lock is held) must not contend with the session lock.
            async with super().acquire_lock(key, ttl_secs=ttl_secs):
                yield
            return
        self.acquire_attempts += 1
        if self.acquire_attempts == 2:
            self.second_attempted.set()
        async with self._test_lock:
            yield


class _Storage:
    """Keep one session and return detached snapshots like a real backend."""

    def __init__(self, session: SessionRecord) -> None:
        self.session = session
        self.loaded_states: list[AgentState] = []
        self.persisted_states: list[AgentState] = []
        self.messages: list[object] = []

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return the latest persisted state as an independent snapshot."""
        if (
            user_id != self.session.user_id
            or agent_id != self.session.agent_id
            or session_id != self.session.id
        ):
            return None
        self.loaded_states.append(self.session.state.model_copy(deep=True))
        return self.session.model_copy(deep=True)

    async def update_session_state(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        state: AgentState,
    ) -> None:
        """Persist the state produced by the lock holder."""
        assert user_id == self.session.user_id
        assert agent_id == self.session.agent_id
        assert session_id == self.session.id
        persisted = state.model_copy(deep=True)
        self.persisted_states.append(persisted)
        self.session.state = persisted

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        message: object,
    ) -> None:
        """Record synthesized failures; successful fake runs emit no reply."""
        assert user_id == self.session.user_id
        assert session_id == self.session.id
        self.messages.append(message.model_copy(deep=True))

    async def get_message(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> Msg | None:
        """Return the latest detached message for result reconciliation."""
        assert user_id == self.session.user_id
        assert session_id == self.session.id
        for message in reversed(self.messages):
            if message.id == message_id:
                return message.model_copy(deep=True)
        return None


class _Access:
    """Return the one agent visible to the test user."""

    def __init__(self, agent: AgentRecord) -> None:
        self.agent = agent

    async def resolve_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord:
        """Resolve a detached agent record."""
        assert user_id == self.agent.user_id
        assert agent_id == self.agent.id
        return self.agent.model_copy(deep=True)


class _WorkspaceManager:
    """Return a minimal workspace handle used during agent assembly."""

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> object:
        """Return the configured workspace without external I/O."""
        del user_id, agent_id, session_id
        assert workspace_id == "workspace-1"
        return SimpleNamespace(workdir="/tmp/agentscope-chat-lock-test")


class _AgentController:
    """Coordinate two fake agents across the contested lock."""

    def __init__(self, bus: _ContendedBus) -> None:
        self.bus = bus
        self.first_running = asyncio.Event()
        self.constructed = 0
        self.observed_states: list[AgentState] = []


def _run_one_message() -> Msg:
    """Return the deterministic state mutation produced by run one."""
    return UserMsg(
        id="run-one-message",
        name="user",
        content=[
            TextBlock(
                id="run-one-text",
                text="persisted by run one",
                created_at="2026-08-04T00:00:00",
                finished_at="2026-08-04T00:00:00",
            ),
        ],
        created_at="2026-08-04T00:00:00",
        finished_at="2026-08-04T00:00:00",
    )


def _agent_class(controller: _AgentController) -> type:
    """Create an Agent-shaped class bound to the test controller."""

    class _Agent:
        """Mutate state without invoking a model or yielding reply events."""

        def __init__(
            self,
            *,
            name: str,
            state: AgentState,
            **_: object,
        ) -> None:
            self.name = name
            self.state = state
            self.ordinal = controller.constructed
            controller.constructed += 1

        async def reply_stream(
            self,
            inputs: object,
        ) -> AsyncGenerator[object, None]:
            """Make run one persist before run two can assemble its agent."""
            del inputs
            controller.observed_states.append(
                self.state.model_copy(deep=True),
            )
            if self.ordinal == 0:
                controller.first_running.set()
                await controller.bus.second_attempted.wait()
                self.state.context.append(_run_one_message())
            if False:
                yield object()

    return _Agent


async def _get_toolkit(**_: object) -> object:
    """Return an inert toolkit handle."""
    return object()


async def _get_model(*_: object, **__: object) -> object:
    """Return an inert model handle."""
    return object()


class TestChatServiceSessionLock(IsolatedAsyncioTestCase):
    """Verify mutable session state is loaded after lock acquisition."""

    async def test_service_binds_and_clears_each_reply_identity(self) -> None:
        """A service task processing A then B ignores an interrupt for A."""
        registry = ChatRunRegistry()
        bus = InMemoryMessageBus()
        ready = asyncio.Event()
        finish = asyncio.Event()
        observed = []

        class ReplyAgent:
            """Yield two replies from one service task without a model."""

            def __init__(
                self,
                name: str,
                state: AgentState,
                **_: object,
            ) -> None:
                """Retain the state assembled by the real service."""
                self.name = name
                self.state = state
                self.count = 0

            async def reply_stream(
                self,
                inputs: object,
            ) -> AsyncGenerator[object, None]:
                """Expose active and ended reply boundaries to the service."""
                del inputs
                reply_id = "A" if self.count == 0 else "B"
                self.count += 1
                self.state.reply_id = reply_id
                yield ReplyStartEvent(
                    session_id="session-1",
                    reply_id=reply_id,
                    name=self.name,
                )
                observed.append(
                    (
                        f"{reply_id}:start",
                        registry.get("session-1", reply_id=reply_id)
                        is asyncio.current_task(),
                    ),
                )
                if reply_id == "B":
                    ready.set()
                    await finish.wait()
                yield ReplyEndEvent(
                    session_id="session-1",
                    reply_id=reply_id,
                    finished_reason="completed",
                )
                observed.append(
                    (
                        f"{reply_id}:end",
                        registry.get("session-1", reply_id=reply_id) is None,
                    ),
                )

        agent = AgentRecord(
            id="agent-1",
            user_id="user-1",
            data=AgentData(name="agent"),
        )
        session = SessionRecord(
            id="session-1",
            user_id="user-1",
            agent_id=agent.id,
            config=SessionConfig(
                workspace_id="workspace-1",
                chat_model_config=ChatModelConfig(
                    type="test",
                    credential_id="c",
                    model="m",
                    parameters={},
                ),
            ),
        )
        service = ChatService(
            _Storage(session),
            _WorkspaceManager(),
            object(),
            object(),
            bus,
            _Access(agent),
            custom_agent_cls=ReplyAgent,
            chat_run_registry=registry,
        )
        async with bus, registry:
            with (
                patch("agentscope.app._service._chat.get_model", _get_model),
                patch(
                    "agentscope.app._service._chat.get_toolkit",
                    _get_toolkit,
                ),
                patch(
                    "agentscope.app._service._chat."
                    "has_pending_inbox_or_release",
                    side_effect=[True, False],
                ),
            ):
                task = registry.spawn(
                    service.run(
                        "user-1",
                        session.id,
                        agent.id,
                        UserMsg(
                            name="user",
                            content="voice",
                            id="voice-input",
                        ),
                    ),
                    session_id=session.id,
                )
                await asyncio.wait_for(ready.wait(), 2)
                self.assertEqual(
                    await service.input_result(
                        "user-1",
                        session.id,
                        agent.id,
                        "voice-input",
                    ),
                    {"state": "reply", "reply_id": "A"},
                )
                CancelDispatcher(bus, registry, object())._interrupt_session(
                    session.id,
                    reply_id="A",
                )
                self.assertEqual(task.cancelling(), 0)
                finish.set()
                await asyncio.wait_for(task, 2)
                replay = await bus.log_read(
                    MessageBusKeys.session_events(session.id),
                )
                self.assertFalse(
                    any(
                        payload["type"] == "REPLY_START"
                        for _, payload in replay
                    ),
                )
                self.assertEqual(
                    await service.input_result(
                        "user-1",
                        session.id,
                        agent.id,
                        "voice-input",
                    ),
                    {"state": "finished", "reply_id": "A"},
                )
        self.assertEqual(
            observed,
            [
                ("A:start", True),
                ("A:end", True),
                ("B:start", True),
                ("B:end", True),
            ],
        )

    async def test_waiter_loads_state_persisted_by_preceding_holder(
        self,
    ) -> None:
        """The second run must assemble from state written by the first."""
        user_id = "user-1"
        agent = AgentRecord(
            id="agent-1",
            user_id=user_id,
            data=AgentData(
                name="agent",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        session = SessionRecord(
            id="session-1",
            user_id=user_id,
            agent_id=agent.id,
            config=SessionConfig(
                workspace_id="workspace-1",
                chat_model_config=ChatModelConfig(
                    type="test",
                    credential_id="credential-1",
                    model="test-model",
                    parameters={},
                ),
            ),
        )
        initial_state = session.state.model_copy(deep=True)
        prepared_state = initial_state.model_copy(deep=True)
        prepared_state.session_id = session.id
        prepared_state.permission_context.working_directories[
            "/tmp/agentscope-chat-lock-test"
        ] = AdditionalWorkingDirectory(
            path="/tmp/agentscope-chat-lock-test",
            source="session",
        )
        expected_state = prepared_state.model_copy(deep=True)
        expected_state.context.append(_run_one_message())
        storage = _Storage(session)
        bus = _ContendedBus()
        controller = _AgentController(bus)
        service = ChatService(
            storage=storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=bus,
            resource_access_service=_Access(agent),
            custom_agent_cls=_agent_class(controller),
        )

        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch(
                "agentscope.app._service._chat.get_model",
                new=_get_model,
            ),
        ):
            first = asyncio.create_task(
                service._run_impl(user_id, session.id, agent.id, None),
            )
            await asyncio.wait_for(controller.first_running.wait(), 1.0)
            second = asyncio.create_task(
                service._run_impl(user_id, session.id, agent.id, None),
            )
            await asyncio.wait_for(
                asyncio.gather(first, second),
                2.0,
            )

        self.assertEqual(
            {
                "loaded_states": storage.loaded_states,
                "observed_states": controller.observed_states,
                "persisted_states": storage.persisted_states,
                "final_state": storage.session.state,
                "messages": storage.messages,
            },
            {
                "loaded_states": [initial_state, expected_state],
                "observed_states": [prepared_state, expected_state],
                "persisted_states": [expected_state, expected_state],
                "final_state": expected_state,
                "messages": [],
            },
        )
