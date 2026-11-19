# -*- coding: utf-8 -*-
# pylint: disable=protected-access,using-constant-test,unnecessary-dunder-call
"""Regression tests for #3990 — an empty inbox must not start a model run.

Several offloaded tool results finishing before the first wake-up run
registers its inbox consumer each enqueue their own ``wake`` trigger, so
the leftover runs arrive once the inbox has already been drained. Each of
them used to call ``reply_stream(inputs=None)`` anyway: a full extra model
call that persisted a duplicate assistant message.

A ``None`` input from the chat endpoint is *not* a wake-up — it is the
documented "continue from current state" trigger — so the guard is keyed
on the trigger kind, not on ``None`` alone.
"""

import asyncio
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._bus_ops import (
    has_pending_inbox_or_release,
    register_inbox_consumer,
)
from agentscope.app._service import ChatService
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
)
from agentscope.message import TextBlock, UserMsg
from agentscope.state import AgentState

_USER = "user-1"
_AGENT = "agent-1"
_SESSION = "session-1"


class _Storage:
    """One session, returned as detached snapshots like a real backend."""

    def __init__(self, session: SessionRecord) -> None:
        self.session = session
        self.messages: list[object] = []

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return a detached copy of the seeded session."""
        if (user_id, agent_id, session_id) != (
            self.session.user_id,
            self.session.agent_id,
            self.session.id,
        ):
            return None
        return self.session.model_copy(deep=True)

    async def update_session_state(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        state: AgentState,
    ) -> None:
        """Persist the state the fake run produced."""
        del user_id, agent_id, session_id
        self.session.state = state.model_copy(deep=True)

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        message: object,
    ) -> None:
        """Record messages written during the run."""
        del user_id, session_id
        self.messages.append(message)


class _Access:
    """Resolve the single agent visible to the test user."""

    def __init__(self, agent: AgentRecord) -> None:
        self.agent = agent

    async def resolve_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord:
        """Return a detached agent record."""
        del user_id, agent_id
        return self.agent.model_copy(deep=True)


class _WorkspaceManager:
    """Return a minimal workspace handle for agent assembly."""

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> object:
        """Return a stable working directory without external I/O."""
        del user_id, agent_id, session_id, workspace_id
        return SimpleNamespace(workdir="/tmp/agentscope-empty-wakeup-test")


class _RunRecorder:
    """Count the runs that actually reach the model."""

    def __init__(self) -> None:
        self.reply_calls = 0


def _agent_class(recorder: _RunRecorder) -> type:
    """An Agent-shaped class whose every reply increments a counter."""

    class _Agent:
        """Emit no events, but record that the model would have run."""

        def __init__(
            self,
            *,
            name: str,
            state: AgentState,
            **_: object,
        ) -> None:
            self.name = name
            self.state = state

        async def reply_stream(
            self,
            inputs: object,
        ) -> AsyncGenerator[object, None]:
            """Count the call and yield nothing."""
            del inputs
            recorder.reply_calls += 1
            if False:
                yield object()

    return _Agent


async def _get_toolkit(**_: object) -> object:
    """Return an inert toolkit handle."""
    return object()


async def _get_model(*_: object, **__: object) -> object:
    """Return an inert model handle."""
    return object()


def _user_msg() -> UserMsg:
    """Return a minimal user message for the wake-up to drain."""
    return UserMsg(
        id="wake-payload",
        name="user",
        content=[
            TextBlock(
                id="wake-text",
                text="consumed by the first wake-up",
                created_at="2026-08-04T00:00:00",
                finished_at="2026-08-04T00:00:00",
            ),
        ],
        created_at="2026-08-04T00:00:00",
        finished_at="2026-08-04T00:00:00",
    )


class TestEmptyInboxWakeup(IsolatedAsyncioTestCase):
    """A ``wake`` trigger with nothing to drain must not call the model."""

    async def asyncSetUp(self) -> None:
        agent = AgentRecord(
            id=_AGENT,
            user_id=_USER,
            data=AgentData(
                name="agent",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        self.session = SessionRecord(
            id=_SESSION,
            user_id=_USER,
            agent_id=_AGENT,
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
        self.storage = _Storage(self.session)
        self.bus = InMemoryMessageBus()
        await self.bus.__aenter__()
        self.recorder = _RunRecorder()
        self.service = ChatService(
            storage=self.storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=self.bus,
            resource_access_service=_Access(agent),
            custom_agent_cls=_agent_class(self.recorder),
        )

    async def asyncTearDown(self) -> None:
        await self.bus.aclose()

    async def _run(
        self,
        *,
        wakeup: bool,
    ) -> None:
        """Drive one run without an input message."""
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
            await asyncio.wait_for(
                self.service._run_impl(
                    _USER,
                    _SESSION,
                    _AGENT,
                    None,
                    wakeup=wakeup,
                ),
                timeout=5.0,
            )

    async def test_empty_inbox_wakeup_skips_the_model(self) -> None:
        """The residual wake-up in #3990 ran a duplicate turn; it must not."""
        await self._run(wakeup=True)

        self.assertEqual(self.recorder.reply_calls, 0)
        self.assertEqual(self.storage.messages, [])
        # The registration taken before the check is released again, so a
        # later delivery still wakes the session instead of deferring to
        # a run that already returned.
        self.assertIsNone(
            await self.bus.registry_get(
                MessageBusKeys.inbox_consumer(_SESSION),
                MessageBusKeys.INBOX_CONSUMER_FIELD,
            ),
        )

    async def test_wakeup_with_pending_payload_is_not_skipped(self) -> None:
        """A payload sitting in the inbox must clear the guard, so the
        wake-up runs instead of returning early."""
        await register_inbox_consumer(self.bus, _SESSION)
        await self.bus.queue_push(
            MessageBusKeys.inbox(_SESSION),
            {"type": "hint", "hint": "tool finished"},
        )

        self.assertTrue(
            await has_pending_inbox_or_release(self.bus, _SESSION),
        )

    async def test_direct_none_continuation_is_not_a_wakeup(self) -> None:
        """``None`` from the chat endpoint means "continue" and must run,
        even with an empty inbox — only wake-up triggers are guarded."""
        await self._run(wakeup=False)

        self.assertEqual(self.recorder.reply_calls, 1)
