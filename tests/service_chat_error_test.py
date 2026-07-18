# -*- coding: utf-8 -*-
"""Regression tests for service-level chat run failures."""
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from agentscope.app._service import ChatService
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys


class ChatServiceErrorTest(IsolatedAsyncioTestCase):
    """Chat failures must terminate the WebUI reply state."""

    async def test_run_failure_publishes_generic_error_event(self) -> None:
        """Unhandled run errors notify subscribers without leaking details."""
        bus = InMemoryMessageBus()
        service = ChatService(
            storage=MagicMock(),
            workspace_manager=MagicMock(),
            scheduler_manager=MagicMock(),
            background_task_manager=MagicMock(),
            message_bus=bus,
            resource_access_service=MagicMock(),
        )

        with patch.object(
            service,
            "_run_impl",
            side_effect=RuntimeError("private provider failure"),
        ):
            async with bus:
                await service.run("user-1", "session-1", "agent-1")
                entries = await bus.log_read(
                    MessageBusKeys.session_events("session-1"),
                )

        self.assertEqual(len(entries), 1)
        event = entries[0][1]
        self.assertEqual(event["type"], "CUSTOM")
        self.assertEqual(event["name"], "chat_run_error")
        self.assertEqual(event["value"], {})
        self.assertNotIn("private provider failure", str(event))
