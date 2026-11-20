# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""Test validation at the actual tool dispatch boundary."""
import asyncio
from typing import Any
from unittest import IsolatedAsyncioTestCase

import jsonschema

from agentscope.message import ToolCallBlock, ToolResultState
from agentscope.state import AgentState
from agentscope.tool import (
    FunctionTool,
    ToolBase,
    Toolkit,
    ToolResponse,
    ToolValidationMiddleware,
)

SCHEMA = {
    "type": "object",
    "properties": {"recipient": {"type": "string"}},
    "required": ["recipient"],
    "additionalProperties": False,
}


class ToolValidationMiddlewareTest(IsolatedAsyncioTestCase):
    """Assert that only a successful validation can produce side effects."""

    def setUp(self) -> None:
        self.sent: list[str] = []

        def send(recipient: str) -> str:
            """Record the actual recipient."""
            self.sent.append(recipient)
            return "sent"

        self.send = send

    @staticmethod
    def allow_support(tool: ToolBase, kwargs: dict[str, Any]) -> str | None:
        """Authorize one recipient independently of the tool schema."""
        if kwargs["recipient"] != "support@example.com":
            return "Recipient not allowed."
        return None

    async def test_allowed_and_denied_calls_through_toolkit(self) -> None:
        """One validator guards two tools through their real dispatch path."""
        toolkit = Toolkit(
            tools=[
                FunctionTool(
                    self.send,
                    name=name,
                    input_schema=SCHEMA,
                    middlewares=[
                        ToolValidationMiddleware(self.allow_support),
                    ],
                )
                for name in ("notify", "notify_backup")
            ],
        )
        for name in ("notify", "notify_backup"):
            for recipient, expected in [
                ("support@example.com", ToolResultState.SUCCESS),
                ("outside@example.com", ToolResultState.DENIED),
            ]:
                with self.subTest(name=name, recipient=recipient):
                    results = [
                        item
                        async for item in toolkit.call_tool(
                            ToolCallBlock(
                                id="notification",
                                name=name,
                                input='{"recipient":"' + recipient + '"}',
                            ),
                            AgentState(),
                        )
                    ]
                    self.assertIsInstance(results[-1], ToolResponse)
                    self.assertEqual(results[-1].state, expected)
        self.assertEqual(self.sent, ["support@example.com"] * 2)

    async def test_schema_rejection_precedes_policy_and_dispatch(self) -> None:
        """Missing, wrong-typed and extra inputs cannot reach the callback."""

        def unexpected_policy(tool: ToolBase, kwargs: dict) -> None:
            """Fail if malformed input reaches application validation."""
            self.fail("policy must not run on invalid inputs")

        tool = FunctionTool(
            self.send,
            input_schema=SCHEMA,
            middlewares=[ToolValidationMiddleware(unexpected_policy)],
        )
        for payload in ({}, {"recipient": 7}, {"recipient": "ok", "extra": 1}):
            with self.subTest(payload=payload):
                chunks = [chunk async for chunk in await tool(**payload)]
                self.assertEqual(chunks[0].state, ToolResultState.DENIED)
        self.assertEqual(self.sent, [])

    async def test_only_none_allows_execution(self) -> None:
        """Empty rejection reasons deny, and false is not implicit approval."""

        def empty_reason(tool: ToolBase, kwargs: dict) -> str:
            """Return a rejection reason that is falsey."""
            return ""

        tool = FunctionTool(
            self.send,
            input_schema=SCHEMA,
            middlewares=[ToolValidationMiddleware(empty_reason)],
        )
        chunks = [
            chunk
            async for chunk in await tool(recipient="support@example.com")
        ]
        self.assertEqual(chunks[0].state, ToolResultState.DENIED)

        def false_result(tool: ToolBase, kwargs: dict) -> Any:
            """Represent an incorrectly implemented validator."""
            return False

        tool = FunctionTool(
            self.send,
            input_schema=SCHEMA,
            middlewares=[ToolValidationMiddleware(false_result)],
        )
        with self.assertRaises(TypeError):
            await anext(await tool(recipient="support@example.com"))
        self.assertEqual(self.sent, [])

    async def test_validator_errors_and_cancellation_do_not_execute(
        self,
    ) -> None:
        """Policy failures must never fall back to permitting the call."""
        for error in (
            RuntimeError("policy unavailable"),
            asyncio.CancelledError(),
        ):
            with self.subTest(error=type(error)):

                def fail(
                    tool: ToolBase,
                    kwargs: dict,
                    failure: BaseException = error,
                ) -> None:
                    """Propagate a policy failure."""
                    raise failure

                tool = FunctionTool(
                    self.send,
                    input_schema=SCHEMA,
                    middlewares=[ToolValidationMiddleware(fail)],
                )
                with self.assertRaises(type(error)):
                    await anext(await tool(recipient="support@example.com"))
        self.assertEqual(self.sent, [])

    async def test_invalid_schema_is_a_programming_error(self) -> None:
        """A broken tool schema must not be reported as an input rejection."""
        tool = FunctionTool(
            self.send,
            input_schema={"type": "not-a-json-schema-type"},
            middlewares=[ToolValidationMiddleware(self.allow_support)],
        )
        with self.assertRaises(jsonschema.SchemaError):
            await anext(await tool(recipient="support@example.com"))
        self.assertEqual(self.sent, [])

    async def test_async_validator_is_rejected(self) -> None:
        """The final policy decision must be synchronous."""

        async def validate(tool: ToolBase, kwargs: dict) -> None:
            """An unsupported asynchronous validator."""

        with self.assertRaises(TypeError):
            ToolValidationMiddleware(validate)

    async def test_validator_must_not_rewrite_arguments(self) -> None:
        """Prevent validating different values from those sent to the tool."""

        def rewrite(tool: ToolBase, kwargs: dict) -> None:
            """A mistaken policy tries to authorize a replacement payload."""
            kwargs["recipient"] = "support@example.com"

        tool = FunctionTool(
            self.send,
            input_schema=SCHEMA,
            middlewares=[ToolValidationMiddleware(rewrite)],
        )
        with self.assertRaises(ValueError):
            await anext(await tool(recipient="outside@example.com"))
        self.assertEqual(self.sent, [])

    async def test_equal_valued_type_changes_are_rejected(self) -> None:
        """Python equality must not hide changes to JSON value types."""

        def send(payload: dict) -> str:
            """Record a real effect if the changed policy input is accepted."""
            self.sent.append(str(payload))
            return "sent"

        for original, replacement in [(0, False), (1, True), (1, 1.0)]:
            with self.subTest(original=original, replacement=replacement):

                def rewrite(
                    tool: ToolBase,
                    kwargs: dict,
                    new_value: Any = replacement,
                ) -> None:
                    """Change a nested argument to a different JSON type."""
                    kwargs["payload"]["enabled"][0] = new_value

                tool = FunctionTool(
                    send,
                    input_schema={"type": "object"},
                    middlewares=[ToolValidationMiddleware(rewrite)],
                )
                with self.assertRaisesRegex(ValueError, "must not modify"):
                    await anext(await tool(payload={"enabled": [original]}))
        self.assertEqual(self.sent, [])

    async def test_coroutine_return_is_not_an_approval(self) -> None:
        """A synchronous wrapper must not accidentally permit async policy."""

        async def allow() -> None:
            """Represent a misplaced asynchronous policy result."""

        def wrapped(tool: ToolBase, kwargs: dict) -> Any:
            """Return the coroutine instead of a synchronous policy result."""
            return allow()

        tool = FunctionTool(
            self.send,
            input_schema=SCHEMA,
            middlewares=[ToolValidationMiddleware(wrapped)],
        )
        with self.assertRaises(TypeError):
            await anext(await tool(recipient="support@example.com"))
        self.assertEqual(self.sent, [])

    async def test_nested_arguments_are_isolated_during_async_execution(
        self,
    ) -> None:
        """External references cannot alter checked nested arguments."""
        entered = asyncio.Event()
        proceed = asyncio.Event()
        retained: list[dict] = []

        def validate(tool: ToolBase, kwargs: dict) -> None:
            """Retain the policy input to exercise reference safety."""
            self.assertEqual(kwargs["payload"]["recipients"], ["allowed"])
            retained.append(kwargs)

        async def send(payload: dict) -> str:
            """Wait after admission but before recording the tool effect."""
            entered.set()
            await proceed.wait()
            self.sent.extend(payload["recipients"])
            return "sent"

        tool = FunctionTool(
            send,
            middlewares=[ToolValidationMiddleware(validate)],
        )
        payload = {"recipients": ["allowed"]}
        stream = await tool(payload=payload)
        pending = asyncio.create_task(anext(stream))
        await asyncio.wait_for(entered.wait(), timeout=5)
        payload["recipients"][0] = "caller rewrite"
        retained[0]["payload"]["recipients"][0] = "validator rewrite"
        proceed.set()
        chunk = await pending
        await stream.aclose()
        self.assertEqual(chunk.content[0].text, "sent")
        self.assertEqual(self.sent, ["allowed"])

    async def test_injected_state_is_preserved_outside_business_schema(
        self,
    ) -> None:
        """Toolkit state is passed by identity, outside business validation."""
        state = AgentState()
        observed: list[AgentState] = []

        def send(recipient: str, _agent_state: AgentState) -> str:
            """Record the exact host state object received by the tool."""
            observed.append(_agent_state)
            self.sent.append(recipient)
            return "sent"

        def validate(tool: ToolBase, kwargs: dict) -> None:
            """Business validation must not see the internal state argument."""
            self.assertEqual(kwargs, {"recipient": "support@example.com"})

        toolkit = Toolkit(
            tools=[
                FunctionTool(
                    send,
                    input_schema=SCHEMA,
                    is_state_injected=True,
                    middlewares=[ToolValidationMiddleware(validate)],
                ),
            ],
        )
        results = [
            item
            async for item in toolkit.call_tool(
                ToolCallBlock(
                    id="state-call",
                    name="send",
                    input='{"recipient":"support@example.com"}',
                ),
                state,
            )
        ]
        self.assertEqual(results[-1].state, ToolResultState.SUCCESS)
        self.assertIs(observed[0], state)
        self.assertEqual(self.sent, ["support@example.com"])
