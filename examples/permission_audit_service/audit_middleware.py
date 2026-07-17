# -*- coding: utf-8 -*-
"""Application-level audit middleware for permission decisions."""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from agentscope.middleware import MiddlewareBase
from agentscope.permission import PermissionDecision

if TYPE_CHECKING:
    from agentscope.agent import Agent

logger = logging.getLogger("permission_audit")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)

PermissionAuditSink = Callable[[dict[str, Any]], Awaitable[None]]


async def console_audit_sink(record: dict[str, Any]) -> None:
    """Write one compact JSON audit record per log line."""
    logger.info(json.dumps(record, ensure_ascii=False))


def _now_iso() -> str:
    """Return the current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _decision_summary(decision: PermissionDecision) -> dict[str, Any]:
    """Serialize the non-input fields used by this audit example."""
    return {
        "behavior": decision.behavior.value,
        "reason": decision.decision_reason,
        "bypass_immune": decision.bypass_immune,
    }


class PermissionAuditMiddleware(MiddlewareBase):
    """Emit the final permission decision returned by the middleware chain.

    Tenant and session identifiers are supplied by the application's
    ``extra_agent_middlewares`` factory. Raw tool-call input is deliberately
    excluded from the record.
    """

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

    async def on_check_permission(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[PermissionDecision]],
    ) -> PermissionDecision:
        """Record and return the final decision without changing it."""
        decision = await next_handler(**input_kwargs)
        tool_call = input_kwargs["tool_call"]
        tool = input_kwargs["tool"]
        record = {
            "event": "permission_decision",
            "observed_at": _now_iso(),
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "reply_id": agent.state.reply_id,
            "tool_call_id": tool_call.id,
            "tool_name": tool.name,
            "mode": agent.state.permission_context.mode.value,
            "decision": _decision_summary(decision),
        }
        await self._sink(record)
        return decision
