# -*- coding: utf-8 -*-
"""Derive namespace strings from user/session identifiers."""
from __future__ import annotations

from typing import Any


class NamespaceFactory:
    """Builds filesystem namespaces from runtime context."""

    @staticmethod
    def get_namespace(
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        parts = ["agentscope", "fs"]
        if user_id:
            parts.append(f"u:{user_id}")
        if session_id:
            parts.append(f"s:{session_id}")
        return ":".join(parts)

    @classmethod
    def from_runtime_context(cls, ctx: dict[str, Any]) -> str:
        return cls.get_namespace(
            user_id=ctx.get("user_id"),
            session_id=ctx.get("session_id"),
        )
