# -*- coding: utf-8 -*-
"""Utils for agents in agentscope."""
from typing import Any


class _AsyncNullContext:
    """An async null context manager."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        return None
