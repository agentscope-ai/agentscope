# -*- coding: utf-8 -*-
"""Only user-authored messages may enter the editable chat queue."""

from typing import Any
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.app._router._chat import chat_router
from agentscope.app.deps import (
    get_current_user_id,
    get_message_bus,
    get_storage,
)


class UpdateChatQueueItemRoleTest(TestCase):
    """The queue-edit endpoint must not accept forged message roles."""

    def setUp(self) -> None:
        """Mount the real router with side-effect-free dependencies."""
        storage = Mock()
        storage.get_session = AsyncMock(return_value=object())

        bus = Mock()

        def get_bus() -> Mock:
            """Return the side-effect-free bus used by the route."""
            return bus

        app = FastAPI()
        app.include_router(chat_router)
        app.dependency_overrides[get_current_user_id] = lambda: "alice"
        app.dependency_overrides[get_storage] = lambda: storage
        app.dependency_overrides[get_message_bus] = get_bus

        patcher = patch(
            "agentscope.app._router._chat.update_chat_input",
            new_callable=AsyncMock,
            return_value=[],
        )
        self.update_chat_input = patcher.start()
        self.addCleanup(patcher.stop)

        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    @staticmethod
    def _message(role: str) -> dict:
        """Build a minimal JSON message accepted by ``Msg`` parsing."""
        return {
            "name": "alice",
            "role": role,
            "content": [{"type": "text", "text": f"message from {role}"}],
        }

    def _patch_item(self, input_: object) -> Any:
        """Send one queue-edit request for the given input payload."""
        return self.client.patch(
            "/chat/queue/item-1",
            headers={"X-User-ID": "alice"},
            json={
                "agent_id": "agent-1",
                "session_id": "session-1",
                "input": input_,
            },
        )

    def test_rejects_server_authored_roles(self) -> None:
        """System, assistant, and mixed lists are rejected before mutation."""
        payloads = [
            self._message("system"),
            self._message("assistant"),
            [self._message("user"), self._message("assistant")],
        ]

        responses = [self._patch_item(payload) for payload in payloads]

        self.assertEqual([_.status_code for _ in responses], [422, 422, 422])
        self.assertTrue(
            all("role='user'" in _.text for _ in responses),
        )
        self.update_chat_input.assert_not_awaited()

    def test_accepts_user_message(self) -> None:
        """An ordinary user message still reaches the queue mutation."""
        response = self._patch_item(self._message("user"))

        self.assertEqual(response.status_code, 200, response.text)
        self.update_chat_input.assert_awaited_once()
        await_args = self.update_chat_input.await_args
        assert await_args is not None
        self.assertEqual(await_args.args[-1].role, "user")

    def test_accepts_user_message_list(self) -> None:
        """A non-empty list of user messages remains valid input."""
        response = self._patch_item(
            [self._message("user"), self._message("user")],
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.update_chat_input.assert_awaited_once()
        await_args = self.update_chat_input.await_args
        assert await_args is not None
        self.assertEqual(
            [message.role for message in await_args.args[-1]],
            ["user", "user"],
        )
