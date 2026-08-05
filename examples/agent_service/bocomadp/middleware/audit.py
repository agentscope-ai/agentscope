# -*- coding: utf-8 -*-
"""审计留痕中间件。

记录每次 agent reply 的起止时间、输入摘要、工具调用与输出摘要，
以 JSONL 行写入 ``AuditConfig.log_path``。

这是企业合规的最低要求：所有 AI 行为必须可追溯。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Callable

from agentscope.middleware import MiddlewareBase

from ..config import get_audit_config


def _safe_str(obj: Any, max_len: int = 500) -> str:
    """安全转字符串并截断，避免审计日志膨胀。"""
    try:
        text = str(obj)
    except Exception:
        return "<unserializable>"
    return text if len(text) <= max_len else text[:max_len] + "...(truncated)"


class AuditMiddleware(MiddlewareBase):
    """记录 agent 完整调用链的审计日志。

    由 :func:`bocomadp.middleware.factory.build_enterprise_middlewares`
    按会话主动构建（user_id / session_id 构造传入）；每个 reply 的
    临时状态（起始时间、工具调用列表）存放在 ``on_reply`` 局部变量中，
    天然隔离并发会话。未显式传入 user_id / session_id 时，运行时从
    ``agent.state`` 回退读取。
    """

    def __init__(self, user_id: str = "", session_id: str = "") -> None:
        self._user_id = user_id
        self._session_id = session_id

    async def on_reply(
        self,
        agent: "Any",  # Agent
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        start_ts = time.monotonic()
        tool_calls: list[dict[str, Any]] = []
        user_id = self._user_id or getattr(
            getattr(agent, "state", None), "user_id", "",
        )
        session_id = self._session_id or getattr(
            getattr(agent, "state", None), "session_id", "",
        )
        self._log_start(agent, input_kwargs, user_id, session_id)

        final_output = ""
        try:
            async for event in next_handler():
                # 捕获工具调用事件用于审计
                event_type = getattr(event, "type", None)
                event_name = getattr(event_type, "name", str(event_type))
                if "TOOL" in event_name:
                    tool_calls.append(
                        {"event": event_name, "detail": _safe_str(event, 300)},
                    )
                # 捕获最终文本输出
                if event_name == "REPLY_END":
                    final_output = _safe_str(event, 1000)
                yield event
        finally:
            self._log_end(
                agent,
                final_output,
                user_id,
                session_id,
                start_ts,
                tool_calls,
            )

    def _log_start(
        self,
        agent: Any,
        input_kwargs: dict,
        user_id: str,
        session_id: str,
    ) -> None:
        inputs = input_kwargs.get("inputs")
        entry = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "event": "reply_start",
            "user_id": user_id,
            "session_id": session_id,
            "agent_name": getattr(agent, "name", ""),
            "input_summary": _safe_str(inputs, 500),
        }
        self._write(entry)

    def _log_end(
        self,
        agent: Any,
        output: str,
        user_id: str,
        session_id: str,
        start_ts: float,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        duration_ms = int((time.monotonic() - start_ts) * 1000)
        entry = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "event": "reply_end",
            "user_id": user_id,
            "session_id": session_id,
            "agent_name": getattr(agent, "name", ""),
            "duration_ms": duration_ms,
            "tool_calls": tool_calls,
            "output_summary": output,
        }
        self._write(entry)

    def _write(self, entry: dict[str, Any]) -> None:
        cfg = get_audit_config()
        if not cfg.enabled:
            return
        path = Path(cfg.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
