# -*- coding: utf-8 -*-
import asyncio
import pytest


def test_bing_mobile_search_e2e_returns_results() -> None:
    try:
        from playwright.async_api import async_playwright  # type: ignore
        _ = async_playwright
    except Exception as e:  # pragma: no cover - env dependent
        pytest.skip(f"Playwright not available: {e}")

    from agentscope.tool import search_bing, ToolResponse  # type: ignore

    async def _run() -> None:
        # Use an organic query that should yield results
        query = "who is speed"
        res: ToolResponse = await search_bing(query)  # type: ignore
        assert res.content, "ToolResponse.content is empty"

        text = "\n".join(
            b.get("text", "")
            for b in res.content
            if b.get("type") == "text"
        )
        # Print raw text so the caller sees real results
        print("=== E2E Bing Results (who is speed) ===")
        print(text)
        print("=== END ===")

        import re
        assert re.search(r"https?://\S+", text) is not None, (
            "Expected at least one result URL in output",
        )

    asyncio.run(_run())
