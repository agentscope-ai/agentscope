# -*- coding: utf-8 -*-
"""Tests for the permission audit service example."""
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
)

_EXAMPLE_DIR = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "permission_audit_service"
)


def _load_example_module(module_name: str) -> ModuleType:
    """Load an example module by filename without requiring a package."""
    path = _EXAMPLE_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo_tool_module = _load_example_module("demo_tool")
PermissionAuditDemoTool = demo_tool_module.PermissionAuditDemoTool

audit_middleware_module = _load_example_module("audit_middleware")
PermissionAuditMiddleware = audit_middleware_module.PermissionAuditMiddleware


def _agent(reply_id: str = "reply-1") -> SimpleNamespace:
    """Build the agent state fields consumed by the example middleware."""
    permission_context = SimpleNamespace(mode=PermissionMode.DEFAULT)
    state = SimpleNamespace(
        reply_id=reply_id,
        permission_context=permission_context,
    )
    return SimpleNamespace(state=state)


def _input_kwargs(secret: str = "secret-token-123") -> dict[str, Any]:
    """Build permission-hook metadata containing a sensitive test value."""
    return {
        "tool_call": ToolCallBlock(
            id="call-1",
            name="PermissionAuditDemoTool",
            input=json.dumps({"decision": "allow", "label": secret}),
        ),
        "tool": PermissionAuditDemoTool(),
        "tool_input": {"decision": "allow", "label": secret},
    }


class _CollectSink:
    """Async sink that collects records into a list."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    async def __call__(self, record: dict) -> None:
        self.records.append(record)


class PermissionAuditMiddlewareTest(IsolatedAsyncioTestCase):
    """The example observes the final decision without altering it."""

    async def test_records_and_returns_final_decision(self) -> None:
        """The middleware delegates once and returns the same decision."""
        sink = _CollectSink()
        middleware = PermissionAuditMiddleware(
            "user-1",
            "agent-1",
            "s-1",
            sink,
        )
        decision = PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
            decision_reason="allow rule matched",
        )
        calls = 0
        forwarded_kwargs: dict[str, Any] = {}

        async def next_handler(**kwargs: Any) -> PermissionDecision:
            nonlocal calls
            calls += 1
            forwarded_kwargs.update(kwargs)
            return decision

        input_kwargs = _input_kwargs()
        returned = await middleware.on_check_permission(
            agent=_agent(reply_id="reply-9"),
            input_kwargs=input_kwargs,
            next_handler=next_handler,
        )

        self.assertIs(returned, decision)
        self.assertEqual(calls, 1)
        self.assertDictEqual(forwarded_kwargs, input_kwargs)
        self.assertEqual(len(sink.records), 1)
        record = sink.records[0]
        self.assertEqual(record["event"], "permission_decision")
        self.assertEqual(record["user_id"], "user-1")
        self.assertEqual(record["agent_id"], "agent-1")
        self.assertEqual(record["session_id"], "s-1")
        self.assertEqual(record["reply_id"], "reply-9")
        self.assertEqual(record["tool_call_id"], "call-1")
        self.assertEqual(record["tool_name"], "PermissionAuditDemoTool")
        self.assertEqual(record["mode"], "default")
        self.assertEqual(record["decision"]["behavior"], "allow")

    async def test_record_excludes_raw_tool_input(self) -> None:
        """The audit record does not copy raw model or parsed tool input."""
        sink = _CollectSink()
        middleware = PermissionAuditMiddleware("u", "a", "s", sink)

        async def next_handler(**_kwargs: Any) -> PermissionDecision:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message="confirmation required",
            )

        await middleware.on_check_permission(
            agent=_agent(),
            input_kwargs=_input_kwargs(),
            next_handler=next_handler,
        )

        serialized = json.dumps(sink.records[0], default=str)
        self.assertNotIn("secret-token-123", serialized)
        self.assertNotIn("tool_input", sink.records[0])
        self.assertNotIn("input", sink.records[0])

    async def test_sink_exception_propagates(self) -> None:
        """The example intentionally stops the call if its sink fails."""

        async def failing_sink(record: dict) -> None:
            raise RuntimeError("sink unavailable")

        middleware = PermissionAuditMiddleware("u", "a", "s", failing_sink)

        async def next_handler(**_kwargs: Any) -> PermissionDecision:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="allowed",
            )

        with self.assertRaisesRegex(RuntimeError, "sink unavailable"):
            await middleware.on_check_permission(
                agent=_agent(),
                input_kwargs=_input_kwargs(),
                next_handler=next_handler,
            )


class PermissionAuditDemoToolTest(IsolatedAsyncioTestCase):
    """The side-effect-free demo emits all final permission behaviors."""

    async def test_engine_returns_selected_final_behavior(self) -> None:
        """DEFAULT mode resolves ALLOW, ASK, and DENY as demonstrated."""
        tool = PermissionAuditDemoTool()
        engine = PermissionEngine(PermissionContext())
        for behavior in (
            PermissionBehavior.ALLOW,
            PermissionBehavior.ASK,
            PermissionBehavior.DENY,
        ):
            decision = await engine.check_permission(
                tool,
                {"decision": behavior.value, "label": "example"},
            )
            self.assertEqual(decision.behavior, behavior)

    async def test_call_has_no_side_effects(self) -> None:
        """Executing the demo tool only returns an acknowledgement."""
        chunk = await PermissionAuditDemoTool().call(
            decision="allow",
            label="hello",
        )
        self.assertIsInstance(chunk.content[0], TextBlock)
        self.assertIn("decision=allow", chunk.content[0].text)


class PermissionAuditServiceSmokeTest(IsolatedAsyncioTestCase):
    """The runnable service module imports when optional services exist."""

    async def test_import_main_app(self) -> None:
        """Importing ``main:app`` should not hide code errors."""
        try:
            main_module = _load_example_module("main")
        except (ImportError, ConnectionError, OSError) as exc:
            self.skipTest(f"cannot import example main (deps/Redis?): {exc}")
        self.assertTrue(hasattr(main_module, "app"))
        self.assertIsNotNone(main_module.permission_audit_factory)
        self.assertIsNotNone(main_module.permission_audit_demo_tools)
