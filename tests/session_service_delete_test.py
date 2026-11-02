# -*- coding: utf-8 -*-
"""Session deletion preserves unowned state and cleans up owned state."""

from contextlib import AsyncExitStack
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.agent import ContextConfig, ReActConfig
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
