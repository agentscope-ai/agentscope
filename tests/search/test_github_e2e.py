# -*- coding: utf-8 -*-
"""E2E test for the GitHub search tool with README snippet.

Success criterion: at least one GitHub repo URL detected in the output.
"""
from __future__ import annotations

import asyncio
import pytest

try:  # pragma: no cover - env dependent
    import playwright  # type: ignore
except Exception:  # noqa: BLE001
    playwright = None

from agentscope.tool import search_github, ToolResponse  # type: ignore


def test_github_search_e2e_returns_results() -> None:
    if playwright is None:
        pytest.skip("Playwright not available in environment")

    async def _run() -> None:
        res: ToolResponse = await search_github("who is speed")  # type: ignore
        text = "\n".join(
            b.get("text", "")
            for b in res.content
            if b.get("type") == "text"
        )
        print("=== E2E GitHub Results (who is speed) ===")
        print(text)
        print("=== END ===")

        import re
        pattern = r"https://github\.com/[^/\s]+/[^/\s]+"
        assert re.search(pattern, text) is not None, (
            "Expected at least one GitHub repo URL in output",
        )

    asyncio.run(_run())
