# -*- coding: utf-8 -*-
"""Application-level audit middleware for the permission audit example.

``PermissionAuditMiddleware`` converts framework permission events into
application-owned JSON audit records and forwards them to a sink. It
demonstrates the read-only observer contract: it never mutates the
agent, tool_call, tool_input, evaluation, or rules.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from agentscope.message import ToolCallBlock
from agentscope.middleware import MiddlewareBase
from agentscope.permission import (
    PermissionDecision,
    PermissionEvaluation,
    PermissionRule,
)
from agentscope.tool import ToolBase

if TYPE_CHECKING:
    from agentscope.agent import Agent

logger = logging.getLogger("permission_audit")
# Configure explicitly so audit records are emitted even when the host
# application has not configured the root logger (the default level is
# WARNING, which would silently drop INFO-level audit records).
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)

PermissionAuditSink = Callable[[dict[str, Any]], Awaitable[None]]


async def console_audit_sink(record: dict[str, Any]) -> None:
    """Default sink: one compact JSON object per log line."""
    logger.info(json.dumps(record, ensure_ascii=False))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decision_summary(decision: PermissionDecision) -> dict[str, Any]:
    return {
        "behavior": decision.behavior.value,
        "reason": decision.decision_reason,
        "bypass_immune": decision.bypass_immune,
    }


class PermissionAuditMiddleware(MiddlewareBase):
    """Audit middleware observing permission decisions and confirmations.

    Receives tenant/session identity from the ``extra_agent_middlewares``
    factory and emits a JSON record per event to the configured sink.
    Records deliberately exclude raw ``tool_input`` and raw rule content.
    """

    # pylint: disable=unused-argument

    def __init__(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        sink: PermissionAuditSink = console_audit_sink,
    ) -> None:
        self.user_id = user_id
        self.agent_id = agent_id
        self.session_id = session_id
        self._sink = sink

    async def on_permission_decision(
        self,
        agent: "Agent",
        tool_call: ToolCallBlock,
        tool: ToolBase,
        tool_input: dict[str, Any],
        evaluation: PermissionEvaluation,
    ) -> None:
        """Emit a JSON record for a permission decision."""
        effective = evaluation.effective_decision
        candidate = evaluation.candidate_decision
        record = {
            "event": "permission_decision",
            "observed_at": _now_iso(),
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "reply_id": agent.state.reply_id,
            "tool_call_id": tool_call.id,
            "tool_name": tool.name,
            "mode": evaluation.mode.value,
            "resolution": evaluation.resolution.value,
            "effective": _decision_summary(effective),
            "candidate": _decision_summary(candidate) if candidate else None,
        }
        await self._sink(record)

    async def on_permission_confirmation(
        self,
        agent: "Agent",
        tool_call: ToolCallBlock,
        confirmed: bool,
        rules: list[PermissionRule],
    ) -> None:
        """Emit a JSON record for a user confirmation."""
        record = {
            "event": "permission_confirmation",
            "observed_at": _now_iso(),
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "reply_id": agent.state.reply_id,
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.name,
            "confirmed": confirmed,
            "accepted_rule_count": len(rules),
        }
        await self._sink(record)
