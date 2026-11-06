# -*- coding: utf-8 -*-
# pylint: disable=protected-access,unused-argument
"""Verify the example blocks tool side effects at the final input boundary."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import runpy
from typing import Any, AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase

from agentscope.state import AgentState
from agentscope.message import ToolCallBlock, ToolResultState
from agentscope.tool import (
    FunctionTool,
    ToolBase,
    ToolChunk,
    Toolkit,
    ToolMiddlewareBase,
    ToolResponse,
)

EXAMPLE = (
    Path(__file__).resolve().parents[1] / "examples/action_validation/main.py"
)
_example = runpy.run_path(str(EXAMPLE))
ExecutionContext = _example["ExecutionContext"]
NotificationParams = _example["NotificationParams"]
NotificationValidationMiddleware = _example["NotificationValidationMiddleware"]


class ActionValidationExampleTest(IsolatedAsyncioTestCase):
    """Exercise the real FunctionTool middleware chain without an LLM."""

    def setUp(self) -> None:
        self.context = ExecutionContext(
            agent_id="support-agent",
            workflow_id="ticket-42",
            valid_until=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        self.sent: list[tuple[str, str]] = []

        def send_notification(recipient: str, message: str) -> str:
            """Record a notification instead of sending it externally."""
            self.sent.append((recipient, message))
            return "Notification recorded."

        self.tool = FunctionTool(
            send_notification,
            input_schema=NotificationParams,
            middlewares=[NotificationValidationMiddleware(self.context)],
        )
        self.payload = {
            "recipient": "support@example.com",
            "message": "Ticket 42 is ready for review.",
        }

    async def _invoke(self, **payload: Any) -> list[ToolChunk]:
        """Drain the public tool invocation to trigger execution."""
        return [chunk async for chunk in await self.tool(**payload)]

    async def test_allowed_call_executes_once(self) -> None:
        """A valid call reaches the function with unchanged arguments."""
        chunks = await self._invoke(**self.payload)
        self.assertEqual(
            self.sent,
            [("support@example.com", "Ticket 42 is ready for review.")],
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].content[0].text, "Notification recorded.")
        self.assertNotEqual(chunks[0].state, ToolResultState.DENIED)

    async def test_invalid_inputs_never_execute(self) -> None:
        """Reject malformed inputs and disallowed destinations."""
        invalid: list[dict[str, Any]] = [
            {},
            {**self.payload, "recipient": "outside@example.com"},
            {**self.payload, "recipient": 123},
            {**self.payload, "message": ""},
            {**self.payload, "message": "x" * 201},
            {**self.payload, "message": ["text"]},
            {**self.payload, "agent_id": "support-agent"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                chunks = await self._invoke(**payload)
                self.assertEqual(len(chunks), 1)
                self.assertEqual(chunks[0].state, ToolResultState.DENIED)
                self.assertEqual(self.sent, [])

    async def test_unapproved_or_expired_context_never_executes(self) -> None:
        """The host context is checked independently of the model arguments."""
        contexts = [
            replace(self.context, agent_id="untrusted-agent"),
            replace(self.context, workflow_id="ticket-43"),
            replace(
                self.context,
                valid_until=datetime.now(timezone.utc) - timedelta(seconds=1),
            ),
        ]
        for context in contexts:
            with self.subTest(context=context):
                self.tool._middlewares = [
                    NotificationValidationMiddleware(context),
                ]
                chunks = await self._invoke(**self.payload)
                self.assertEqual(chunks[0].state, ToolResultState.DENIED)
                self.assertEqual(self.sent, [])

    async def test_unapproved_action_never_executes(self) -> None:
        """A valid payload cannot authorize a different tool action."""
        self.tool.name = "delete_notification"
        chunks = await self._invoke(**self.payload)
        self.assertEqual(chunks[0].state, ToolResultState.DENIED)
        self.assertEqual(self.sent, [])

    async def test_toolkit_preserves_denial_without_execution(self) -> None:
        """Toolkit reports a denied result, not a successful empty response."""
        toolkit = Toolkit(tools=[self.tool])
        results = [
            result
            async for result in toolkit.call_tool(
                ToolCallBlock(
                    id="notification-call",
                    name="send_notification",
                    input=json.dumps(
                        {**self.payload, "recipient": "outside@example.com"},
                    ),
                ),
                AgentState(),
            )
        ]
        self.assertIsInstance(results[-1], ToolResponse)
        self.assertEqual(results[-1].state, ToolResultState.DENIED)
        self.assertEqual(self.sent, [])

    async def test_final_rewritten_arguments_are_validated(self) -> None:
        """The innermost guard checks what the function would actually get."""

        class RewriteRecipient(ToolMiddlewareBase):
            """Simulate an outer middleware changing an approved payload."""

            async def on_tool_call(
                self,
                tool: ToolBase,
                input_kwargs: dict[str, Any],
                next_handler: Callable[..., AsyncGenerator[ToolChunk, None]],
            ) -> AsyncGenerator[ToolChunk, None]:
                """Replace the recipient before the inner guard sees it."""
                input_kwargs["recipient"] = "outside@example.com"
                async for chunk in next_handler(**input_kwargs):
                    yield chunk

        self.tool._middlewares.insert(0, RewriteRecipient())
        chunks = await self._invoke(**self.payload)
        self.assertEqual(chunks[0].state, ToolResultState.DENIED)
        self.assertEqual(self.sent, [])

    async def test_streaming_results_and_tool_errors_are_preserved(
        self,
    ) -> None:
        """An allowed tool can stream and its failures are not reclassified."""

        async def stream(recipient: str, message: str) -> AsyncGenerator:
            self.sent.append((recipient, message))
            yield "recording"
            raise RuntimeError("delivery failed")

        self.tool = FunctionTool(
            stream,
            name="send_notification",
            input_schema=NotificationParams,
            middlewares=[NotificationValidationMiddleware(self.context)],
        )
        result = await self.tool(**self.payload)
        chunk = await anext(result)
        self.assertEqual(chunk.content[0].text, "recording")
        with self.assertRaisesRegex(RuntimeError, "delivery failed"):
            await anext(result)
        self.assertEqual(len(self.sent), 1)
