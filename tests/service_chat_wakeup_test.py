# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for wake-driven :class:`ChatService` runs."""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.app._service._chat import ChatService


def _service(message_bus: object) -> ChatService:
    """Build a chat service with only the dependency under test."""
    return ChatService(
        storage=None,
        workspace_manager=None,
        scheduler_manager=None,
        background_task_manager=None,
        message_bus=message_bus,
        resource_access_service=None,
    )


class TestEmptyWakeupGuard(IsolatedAsyncioTestCase):
    """Wake-only runs should execute only when inbox work remains."""

    async def test_empty_wakeup_is_skipped(self) -> None:
        """A wake with no pending inbox entries does not need an agent run."""
        bus = SimpleNamespace(inbox_len=AsyncMock(return_value=0))

        skipped = await _service(bus)._skip_empty_wakeup("session-1", None)

        self.assertTrue(skipped)
        bus.inbox_len.assert_awaited_once_with("session-1")

    async def test_wakeup_with_pending_inbox_is_not_skipped(self) -> None:
        """A pending completion result must be delivered to the agent."""
        bus = SimpleNamespace(inbox_len=AsyncMock(return_value=1))

        skipped = await _service(bus)._skip_empty_wakeup("session-1", None)

        self.assertFalse(skipped)

    async def test_unknown_inbox_length_is_not_skipped(self) -> None:
        """Custom transports without queue inspection preserve delivery."""
        bus = SimpleNamespace(inbox_len=AsyncMock(return_value=None))

        skipped = await _service(bus)._skip_empty_wakeup("session-1", None)

        self.assertFalse(skipped)

    async def test_input_message_bypasses_empty_wakeup_guard(self) -> None:
        """Real run inputs are never suppressed by inbox state."""
        bus = SimpleNamespace(inbox_len=AsyncMock(return_value=0))

        skipped = await _service(bus)._skip_empty_wakeup(
            "session-1",
            object(),
        )

        self.assertFalse(skipped)
        bus.inbox_len.assert_not_awaited()
