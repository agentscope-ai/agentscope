# -*- coding: utf-8 -*-
"""Tests for the session router."""
import asyncio
from unittest import IsolatedAsyncioTestCase

from agentscope.app._router._schema import UpdateSessionRequest
from agentscope.app._router._session import update_session
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import SessionConfig, SessionRecord
from agentscope.permission import PermissionMode
from agentscope.state import AgentState


class _RacingStorage:
    """Minimal storage double that exposes stale concurrent reads."""

    def __init__(self) -> None:
        """Initialize a session and controls for the first read."""
        self.session = SessionRecord(
            id="session-1",
            user_id="user-1",
            agent_id="agent-1",
            config=SessionConfig(
                workspace_id="workspace-1",
                name="original",
            ),
            state=AgentState(session_id="session-1"),
        )
        self.first_read = asyncio.Event()
        self.release_first_read = asyncio.Event()
        self.read_count = 0

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord:
        """Return a snapshot, pausing the first reader.

        Args:
            user_id (`str`):
                Session owner ID.
            agent_id (`str`):
                Session agent ID.
            session_id (`str`):
                Session ID.

        Returns:
            `SessionRecord`:
                A detached snapshot of the current session.
        """
        del user_id, agent_id, session_id
        snapshot = self.session.model_copy(deep=True)
        self.read_count += 1
        if self.read_count == 1:
            self.first_read.set()
            await self.release_first_read.wait()
        return snapshot

    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        config: SessionConfig,
        state: AgentState,
        session_id: str,
    ) -> SessionRecord:
        """Replace the stored config and state.

        Args:
            user_id (`str`):
                Session owner ID.
            agent_id (`str`):
                Session agent ID.
            config (`SessionConfig`):
                Replacement session configuration.
            state (`AgentState`):
                Replacement session state.
            session_id (`str`):
                Session ID.

        Returns:
            `SessionRecord`:
                A detached snapshot of the updated session.
        """
        del user_id, agent_id, session_id
        self.session = self.session.model_copy(
            update={
                "config": config,
                "state": state,
            },
            deep=True,
        )
        return self.session.model_copy(deep=True)


class TestUpdateSession(IsolatedAsyncioTestCase):
    """Concurrent update behavior for the session PATCH route."""

    async def test_concurrent_updates_preserve_disjoint_fields(self) -> None:
        """Two concurrent PATCH requests preserve both field updates."""
        storage = _RacingStorage()
        message_bus = InMemoryMessageBus()
        access = object()
        expected = storage.session.model_copy(deep=True)

        rename_task = asyncio.create_task(
            update_session(
                session_id="session-1",
                body=UpdateSessionRequest(name="renamed"),
                agent_id="agent-1",
                user_id="user-1",
                storage=storage,
                access=access,
                message_bus=message_bus,
            ),
        )
        await storage.first_read.wait()

        permission_task = asyncio.create_task(
            update_session(
                session_id="session-1",
                body=UpdateSessionRequest(
                    permission_mode=PermissionMode.BYPASS,
                ),
                agent_id="agent-1",
                user_id="user-1",
                storage=storage,
                access=access,
                message_bus=message_bus,
            ),
        )
        await asyncio.sleep(0)
        storage.release_first_read.set()

        await asyncio.gather(rename_task, permission_task)

        expected.config.name = "renamed"
        expected.state.permission_context.mode = PermissionMode.BYPASS
        self.assertEqual(
            storage.session.model_dump(),
            expected.model_dump(),
        )
