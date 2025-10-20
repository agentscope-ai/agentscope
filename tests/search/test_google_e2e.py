# -*- coding: utf-8 -*-
import asyncio
import contextlib
import re

import pytest


def test_google_mobile_search_e2e_returns_results() -> None:
    try:
        from patchright.async_api import async_playwright  # type: ignore
    except Exception as e:  # pragma: no cover - env dependent
        pytest.skip(f"patchright not available: {e}")

    from agentscope.tool import search_google, ToolResponse  # type: ignore

    async def _run() -> None:
        async with async_playwright() as p:  # type: ignore
            try:
                browser = await p.webkit.launch(headless=True)  # type: ignore
            except Exception as e:  # pragma: no cover
                pytest.skip(f"WebKit not installed/launchable: {e}")

            try:
                query = "who is speed"
                res: ToolResponse = await search_google(query, client=browser)  # type: ignore
                assert res.content, "ToolResponse.content is empty"
                text_blocks = [b for b in res.content if b.get("type") == "text"]
                assert text_blocks, "No text blocks returned"
                md = "\n".join(b.get("text", "") for b in text_blocks)
                print("=== E2E Google Results (who is speed) ===")
                print(md)
                print("=== END ===")
                assert re.search(r"https?://\S+", md) is not None, (
                    "Expected at least one result URL in output",
                )
            finally:
                with contextlib.suppress(Exception):
                    await browser.close()  # type: ignore

    asyncio.run(_run())
