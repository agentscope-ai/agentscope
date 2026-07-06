# -*- coding: utf-8 -*-
"""Unit tests for the permission audit example (demo tool + middleware).

The example modules live under ``examples/permission_audit_service/``,
which is not a Python package, so tests load them via ``importlib``.
"""
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.permission import (
    PermissionBehavior,
    PermissionDecision,
    PermissionEvaluation,
    PermissionMode,
    PermissionResolution,
    PermissionRule,
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


def _evaluation(
    behavior: PermissionBehavior = PermissionBehavior.ALLOW,
    resolution: PermissionResolution = PermissionResolution.DIRECT,
    candidate: PermissionDecision | None = None,
    bypass_immune: bool = False,
) -> PermissionEvaluation:
    return PermissionEvaluation(
        mode=PermissionMode.BYPASS,
        effective_decision=PermissionDecision(
            behavior=behavior,
            message="m",
            decision_reason="r",
            bypass_immune=bypass_immune,
        ),
        candidate_decision=candidate,
        resolution=resolution,
    )


def _tool_call(
    tool_name: str = "PermissionAuditDemoTool",
    call_id: str = "call-1",
) -> ToolCallBlock:
    return ToolCallBlock(
        id=call_id,
        name=tool_name,
        input='{"risk": "safety", "label": "x"}',
    )


def _agent(reply_id: str = "reply-1") -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(reply_id=reply_id))


def json_module_dumps(obj: Any) -> str:
    """Serialize ``obj`` to JSON with ``str`` fallback."""
    return json.dumps(obj, default=str)


class _CollectSink:
    """Async sink that collects records into a list."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    async def __call__(self, record: dict) -> None:
        self.records.append(record)


class PermissionAuditDecisionRecordTest(IsolatedAsyncioTestCase):
    """Spec §Validation 1-5: decision records."""

    async def test_direct_decision_emits_candidate_null(self) -> None:
        """Test that a direct decision emits a record with candidate=null."""
        # 1. direct decision emits candidate=null
        sink = _CollectSink()
        mw = PermissionAuditMiddleware("u", "a", "s", sink)
        await mw.on_permission_decision(
            agent=_agent(),
            tool_call=_tool_call(),
            tool=PermissionAuditDemoTool(),
            tool_input={"risk": "ordinary", "label": "x"},
            evaluation=_evaluation(),
        )
        assert len(sink.records) == 1
        assert sink.records[0]["candidate"] is None

    async def test_candidate_and_effective_serialize(self) -> None:
        """Test candidate/effective decisions and resolution serialize."""
        # 2. candidate/effective decisions and resolution serialize correctly
        sink = _CollectSink()
        mw = PermissionAuditMiddleware("u", "a", "s", sink)
        candidate = PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="dangerous",
            decision_reason="safety",
            bypass_immune=True,
        )
        await mw.on_permission_decision(
            agent=_agent(),
            tool_call=_tool_call(),
            tool=PermissionAuditDemoTool(),
            tool_input={"risk": "safety", "label": "x"},
            evaluation=_evaluation(
                behavior=PermissionBehavior.ALLOW,
                resolution=PermissionResolution.BYPASS_ASK_SUPPRESSED,
                candidate=candidate,
            ),
        )
        rec = sink.records[0]
        assert rec["resolution"] == "bypass_ask_suppressed"
        assert rec["effective"]["behavior"] == "allow"
        assert rec["candidate"]["behavior"] == "ask"
        assert rec["candidate"]["bypass_immune"] is True

    async def test_raw_tool_input_never_in_record(self) -> None:
        """Test that raw tool input never appears in the record."""
        # 3. raw tool input and raw model input never appear in the record
        sink = _CollectSink()
        mw = PermissionAuditMiddleware("u", "a", "s", sink)
        sensitive_input = {"risk": "safety", "label": "secret-token-123"}
        await mw.on_permission_decision(
            agent=_agent(),
            tool_call=_tool_call(),
            tool=PermissionAuditDemoTool(),
            tool_input=sensitive_input,
            evaluation=_evaluation(),
        )
        rec = sink.records[0]
        # The sensitive value must not leak into any record field.
        assert "secret-token-123" not in json_module_dumps(rec)
        # tool_input and the raw model input (tool_call.input) are absent.
        assert "tool_input" not in rec
        assert "input" not in rec

    async def test_factory_binds_identifiers(self) -> None:
        """Test that the factory binds user/agent/session identifiers."""
        # 4. factory binds user, agent, and session identifiers correctly
        sink = _CollectSink()
        mw = PermissionAuditMiddleware("user-1", "agent-1", "session-1", sink)
        await mw.on_permission_decision(
            agent=_agent(reply_id="reply-9"),
            tool_call=_tool_call(),
            tool=PermissionAuditDemoTool(),
            tool_input={"risk": "ordinary", "label": "x"},
            evaluation=_evaluation(),
        )
        rec = sink.records[0]
        assert rec["user_id"] == "user-1"
        assert rec["agent_id"] == "agent-1"
        assert rec["session_id"] == "session-1"
        assert rec["reply_id"] == "reply-9"
        assert rec["tool_call_id"] == "call-1"

    async def test_sink_exception_propagates(self) -> None:
        """Test that sink exceptions propagate (fail-closed contract)."""

        # 5. sink exceptions propagate (fail-closed contract)
        class _FailingSink:
            async def __call__(self, record: dict) -> None:
                raise RuntimeError("sink down")

        mw = PermissionAuditMiddleware("u", "a", "s", _FailingSink())
        with self.assertRaises(RuntimeError):
            await mw.on_permission_decision(
                agent=_agent(),
                tool_call=_tool_call(),
                tool=PermissionAuditDemoTool(),
                tool_input={"risk": "ordinary", "label": "x"},
                evaluation=_evaluation(),
            )


class PermissionAuditDemoToolTest(IsolatedAsyncioTestCase):
    """The demo tool emits the right ASK for each risk level."""

    async def test_ordinary_risk_emits_plain_ask(self) -> None:
        """Test that an ordinary risk emits a plain (non-bypass-immune) ASK."""
        decision = await PermissionAuditDemoTool().check_permissions(
            {"risk": "ordinary", "label": "x"},
            context=None,
        )
        assert decision.behavior == PermissionBehavior.ASK
        assert decision.bypass_immune is False

    async def test_safety_risk_emits_bypass_immune_ask(self) -> None:
        """Test that a safety risk emits a bypass-immune ASK."""
        decision = await PermissionAuditDemoTool().check_permissions(
            {"risk": "safety", "label": "x"},
            context=None,
        )
        assert decision.behavior == PermissionBehavior.ASK
        assert decision.bypass_immune is True

    async def test_call_has_no_side_effects(self) -> None:
        """Test that invoking the demo tool returns the labelled chunk."""
        chunk = await PermissionAuditDemoTool()(
            risk="ordinary",
            label="hello",
        )
        assert isinstance(chunk.content[0], TextBlock)
        assert "ordinary" in chunk.content[0].text


class PermissionAuditConfirmationRecordTest(IsolatedAsyncioTestCase):
    """Spec §Validation 6-8: confirmation records."""

    async def test_approval_and_rejection_serialize_separately(self) -> None:
        """Test that approval and rejection serialize as separate records."""
        # 6. approval and rejection serialize as separate confirmation records
        sink = _CollectSink()
        mw = PermissionAuditMiddleware("u", "a", "s", sink)
        rule = PermissionRule(
            tool_name="PermissionAuditDemoTool",
            rule_content=None,
            behavior=PermissionBehavior.ALLOW,
            source="user",
        )
        await mw.on_permission_confirmation(
            agent=_agent(),
            tool_call=_tool_call(),
            confirmed=True,
            rules=[rule],
        )
        await mw.on_permission_confirmation(
            agent=_agent(),
            tool_call=_tool_call(),
            confirmed=False,
            rules=[],
        )
        assert len(sink.records) == 2
        assert sink.records[0]["confirmed"] is True
        assert sink.records[1]["confirmed"] is False

    async def test_confirmation_exposes_rule_count_not_content(self) -> None:
        """Test that confirmation records expose rule count, not content."""
        # 7. confirmation records expose rule count but never raw rule content
        sink = _CollectSink()
        mw = PermissionAuditMiddleware("u", "a", "s", sink)
        rules = [
            PermissionRule(
                tool_name="PermissionAuditDemoTool",
                rule_content="secret-pattern",
                behavior=PermissionBehavior.ALLOW,
                source="user",
            ),
            PermissionRule(
                tool_name="PermissionAuditDemoTool",
                rule_content=None,
                behavior=PermissionBehavior.ALLOW,
                source="user",
            ),
        ]
        await mw.on_permission_confirmation(
            agent=_agent(),
            tool_call=_tool_call(),
            confirmed=True,
            rules=rules,
        )
        rec = sink.records[0]
        assert rec["accepted_rule_count"] == 2
        # Raw rule content must not leak.
        assert "secret-pattern" not in json_module_dumps(rec)
        assert "rules" not in rec

    async def test_confirmation_and_decision_share_correlation(self) -> None:
        """Test that confirmation and decision records share correlation."""
        # 8. confirmation and decision records share correlation identifiers
        sink = _CollectSink()
        mw = PermissionAuditMiddleware("user-1", "agent-1", "session-1", sink)
        await mw.on_permission_decision(
            agent=_agent(reply_id="reply-1"),
            tool_call=_tool_call(call_id="call-1"),
            tool=PermissionAuditDemoTool(),
            tool_input={"risk": "safety", "label": "x"},
            evaluation=_evaluation(resolution=PermissionResolution.DIRECT),
        )
        await mw.on_permission_confirmation(
            agent=_agent(reply_id="reply-1"),
            tool_call=_tool_call(call_id="call-1"),
            confirmed=True,
            rules=[],
        )
        decision_rec, confirm_rec = sink.records
        for field in (
            "user_id",
            "agent_id",
            "session_id",
            "reply_id",
            "tool_call_id",
        ):
            assert decision_rec[field] == confirm_rec[field], field
        assert decision_rec["event"] == "permission_decision"
        assert confirm_rec["event"] == "permission_confirmation"


class PermissionAuditServiceSmokeTest(IsolatedAsyncioTestCase):
    """Importing main:app should not error (smoke). Requires Redis."""

    async def test_import_main_app(self) -> None:
        """Test that importing ``main:app`` succeeds without error."""
        # Skip only on environment-dependent failures (missing optional
        # deps / Redis connection). Real code errors (SyntaxError, etc.)
        # must propagate so the example does not silently break.
        try:
            main_module = _load_example_module("main")
        except (ImportError, ConnectionError, OSError) as exc:
            self.skipTest(f"cannot import example main (deps/Redis?): {exc}")
        assert hasattr(main_module, "app")
        assert main_module.permission_audit_factory is not None
        assert main_module.permission_audit_demo_tools is not None
