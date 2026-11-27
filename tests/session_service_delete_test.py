# -*- coding: utf-8 -*-
"""Session deletion preserves unowned state and cleans up owned state."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._router._schema import UpdateSessionRequest
from agentscope.app._router._session import update_session
from agentscope.app._service import SessionService
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    AsyncSQLAlchemyStorage,
    SessionConfig,
)


class SessionDeleteTest(IsolatedAsyncioTestCase):
    """Exercise deletion across the real storage and bus boundaries."""

    async def asyncSetUp(self) -> None:
        """Create a victim-owned session with a queued message."""
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(InMemoryMessageBus())
        self.storage = await self._stack.enter_async_context(
            AsyncSQLAlchemyStorage(
                "sqlite+aiosqlite:///:memory:",
                create_tables=True,
            ),
        )
        self.user_id = "victim"
        self.agent_id = "agent-1"
        await self.storage.upsert_agent(
            self.user_id,
            AgentRecord(
                id=self.agent_id,
                user_id=self.user_id,
                data=AgentData(
                    name="Victim agent",
                    context_config=ContextConfig(),
                    react_config=ReActConfig(),
                ),
            ),
        )
        self.session = await self.storage.upsert_session(
            user_id=self.user_id,
            agent_id=self.agent_id,
            config=SessionConfig(workspace_id="workspace-1"),
        )
        self.inbox_entry_id = await self.bus.queue_push(
            MessageBusKeys.inbox(self.session.id),
            {"type": "hint", "hint": "pending work"},
        )
        self.events_key = MessageBusKeys.session_events(self.session.id)
        self.checkpoint_key = MessageBusKeys.session_event_checkpoint(
            self.session.id,
        )
        self.event_entry_id = await self.bus.log_append(
            self.events_key,
            {"type": "REPLY_START"},
        )
        await self.bus.registry_set(
            self.checkpoint_key,
            MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
            "cursor-1",
        )
        self.service = SessionService(self.storage, self.bus)

    async def asyncTearDown(self) -> None:
        """Close both in-memory backends."""
        await self._stack.aclose()

    async def _race_patch_and_delete(self, patch_first: bool) -> None:
        """Pause the first SQLite write until its competitor reaches it."""
        sid = self.session.id
        config_key = MessageBusKeys.session_config_lock(sid)
        writing = asyncio.Event()
        resume = asyncio.Event()
        acquire = self.bus.acquire_lock

        @asynccontextmanager
        async def observe_lock(
            key: str,
            **kwargs: int,
        ) -> AsyncIterator[None]:
            if key == config_key and writing.is_set():
                resume.set()
            async with acquire(key, **kwargs):
                yield

        method = "upsert_session" if patch_first else "delete_session"
        write = getattr(self.storage, method)

        async def paused_write(*args: object, **kwargs: object) -> object:
            writing.set()
            await resume.wait()
            return await write(*args, **kwargs)

        rename = update_session(
            sid,
            UpdateSessionRequest(name="renamed"),
            self.agent_id,
            self.user_id,
            self.storage,
            MagicMock(),
            self.bus,
        )
        delete = self.service.delete_session(self.user_id, self.agent_id, sid)
        with (
            patch.object(self.storage, method, side_effect=paused_write),
            patch.object(self.bus, "acquire_lock", observe_lock),
        ):
            async with asyncio.timeout(5):
                first = asyncio.create_task(rename if patch_first else delete)
                await writing.wait()
                second = asyncio.create_task(delete if patch_first else rename)
                # A missing lock lets the competitor finish the race early.
                second.add_done_callback(lambda _: resume.set())
                results = await asyncio.gather(
                    first,
                    second,
                    return_exceptions=True,
                )
        updated, deleted = results if patch_first else reversed(results)
        self.assertIs(deleted, True)
        self.assertIsNone(
            await self.storage.get_session(self.user_id, self.agent_id, sid),
        )
        if patch_first:
            self.assertEqual(updated.config.name, "renamed")
        else:
            self.assertIsInstance(updated, HTTPException)
            self.assertEqual(updated.status_code, 404)

    async def test_patch_before_delete(self) -> None:
        """A PATCH already writing cannot resurrect a deleted session."""
        await self._race_patch_and_delete(patch_first=True)

    async def test_delete_before_patch(self) -> None:
        """A pending PATCH sees 404 after the deletion commits."""
        await self._race_patch_and_delete(patch_first=False)

    async def test_unowned_delete_preserves_run_and_inbox(self) -> None:
        """A 404-equivalent result must have no cross-user side effects."""
        expected_session = self.session.model_dump()
        with patch.object(self.bus, "publish", wraps=self.bus.publish) as sent:
            deleted = await self.service.delete_session(
                "attacker",
                self.agent_id,
                self.session.id,
            )

        self.assertFalse(deleted)
        sent.assert_not_called()
        stored = await self.storage.get_session(
            self.user_id,
            self.agent_id,
            self.session.id,
        )
        self.assertEqual(
            stored.model_dump() if stored is not None else None,
            expected_session,
        )
        entries = await self.bus.queue_drain(
            MessageBusKeys.inbox(self.session.id),
        )
        self.assertEqual(
            entries,
            [(self.inbox_entry_id, {"type": "hint", "hint": "pending work"})],
        )
        self.assertEqual(
            await self.bus.log_read(self.events_key),
            [(self.event_entry_id, {"type": "REPLY_START"})],
        )
        self.assertEqual(
            await self.bus.registry_getall(self.checkpoint_key),
            {MessageBusKeys.SESSION_EVENT_CURSOR_FIELD: "cursor-1"},
        )

    async def test_owned_delete_still_cancels_and_purges(self) -> None:
        """The owner still gets the full cancel, delete and purge flow."""
        with patch.object(self.bus, "publish", wraps=self.bus.publish) as sent:
            deleted = await self.service.delete_session(
                self.user_id,
                self.agent_id,
                self.session.id,
            )

        self.assertTrue(deleted)
        sent.assert_awaited_once_with(
            MessageBusKeys.session_cancel_channel(),
            {"session_id": self.session.id},
        )
        self.assertIsNone(
            await self.storage.get_session(
                self.user_id,
                self.agent_id,
                self.session.id,
            ),
        )
        self.assertEqual(
            await self.bus.queue_drain(MessageBusKeys.inbox(self.session.id)),
            [],
        )
        self.assertEqual(await self.bus.log_read(self.events_key), [])
        self.assertEqual(
            await self.bus.registry_getall(self.checkpoint_key),
            {},
        )
