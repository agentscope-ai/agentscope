# -*- coding: utf-8 -*-
"""Preserve failed tool execution status at the Anthropic API boundary."""
import json
from unittest import IsolatedAsyncioTestCase

import httpx2

from agentscope.credential import AnthropicCredential
from agentscope.formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
)
from agentscope.message import (
    AssistantMsg,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)
from agentscope.model import AnthropicChatModel


def _messages(state: ToolResultState, output: str = "failed") -> list:
    """Build a tool turn whose output alone does not encode its status."""
    return [
        AssistantMsg(
            name="assistant",
            content=[ToolCallBlock(id="call_1", name="validate", input="{}")],
        ),
        AssistantMsg(
            name="assistant",
            content=[
                ToolResultBlock(
                    id="call_1",
                    name="validate",
                    output=output,
                    state=state,
                ),
            ],
        ),
    ]


class AnthropicToolErrorTest(IsolatedAsyncioTestCase):
    """Check both formatters and a real SDK request without network calls."""

    async def test_error_flag_preserves_other_states_and_empty_output(
        self,
    ) -> None:
        """Only ERROR gains a flag; existing result content is preserved."""
        for formatter_type in (
            AnthropicChatFormatter,
            AnthropicMultiAgentFormatter,
        ):
            for state in ToolResultState:
                for output in ("failed", ""):
                    with self.subTest(
                        formatter=formatter_type.__name__,
                        state=state,
                        output=output,
                    ):
                        result = await formatter_type().format(
                            _messages(state, output),
                        )
                        block = result[-1]["content"][0]
                        self.assertEqual(block["tool_use_id"], "call_1")
                        self.assertEqual(
                            block["content"][0]["text"],
                            output or "(empty tool output)",
                        )
                        if state == ToolResultState.ERROR:
                            self.assertIs(block.get("is_error"), True)
                        else:
                            self.assertNotIn("is_error", block)

    async def test_error_flag_reaches_real_sdk_request(self) -> None:
        """The public model sends the failure flag to the transport."""
        captured = []

        def handle(request: httpx2.Request) -> httpx2.Response:
            captured.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "offline",
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handle),
        ) as client:
            model = AnthropicChatModel(
                credential=AnthropicCredential(api_key="offline-test"),
                model="offline",
                stream=False,
                client_kwargs={"http_client": client},
            )
            await model(_messages(ToolResultState.ERROR))
            await model(_messages(ToolResultState.SUCCESS))

        error = captured[0]["messages"][-1]["content"][0]
        success = captured[1]["messages"][-1]["content"][0]
        self.assertIs(error.get("is_error"), True)
        self.assertNotIn("is_error", success)
        self.assertEqual(
            {key: value for key, value in error.items() if key != "is_error"},
            success,
        )
