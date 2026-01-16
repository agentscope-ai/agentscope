# -*- coding: utf-8 -*-
"""E2E test for the Sogou search tool.

Success criterion: at least one URL detected in the output.
Skips if Playwright is not available in the environment.
"""
from __future__ import annotations

import asyncio
import pytest

try:  # pragma: no cover - env dependent
    import playwright  # type: ignore
except Exception:  # noqa: BLE001
    playwright = None

from agentscope.tool import search_sogou, ToolResponse  # type: ignore


def test_sogou_search_e2e_returns_results() -> None:
    if playwright is None:
        pytest.skip("Playwright not available in environment")

    async def _run() -> None:
        res: ToolResponse = await search_sogou("who is speed")  # type: ignore
        text = "\n".join(
            b.get("text", "")
            for b in res.content
            if b.get("type") == "text"
        )
        print("=== E2E Sogou Results (who is speed) ===")
        print(text)
        print("=== END ===")

        import re
        assert re.search(r"https?://\S+", text) is not None, (
            "Expected at least one URL in output",
        )

    asyncio.run(_run())
