# -*- coding: utf-8 -*-
"""Bing mobile web search (implementation file).

Note: the function docstring on `search_bing` is used as the tool description
when registering the tool; keep that one concise and model-facing.
"""
from __future__ import annotations

import asyncio
from typing import List
from urllib.parse import quote_plus

from .._response import ToolResponse
from ...message import TextBlock
from .common import Result, truncate_rows, render_text_list


MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/16.0 Mobile/15E148 Safari/604.1"
)


# type: ignore[no-untyped-def]
async def _extract_results(page) -> List[Result]:
    """Best-effort extraction for Bing results on mobile/desktop DOM.

    We probe common selectors to be resilient to layout variants.
    """
    results: List[Result] = []

    # Prefer mobile container; fall back to generic results container.
    await page.wait_for_selector("#b_results, li.b_algo, main")

    # Candidate item selectors (mobile/desktop)
    item_selectors = [
        "#b_results li.b_algo",
        "#b_results li",
        "li.b_algo",
        "main .b_algo",
    ]

    locator = None
    for sel in item_selectors:
        loc = page.locator(sel)
        if await loc.count() > 0:
            locator = loc
            break
    if locator is None:
        return results

    n = min(await locator.count(), 10)
    for i in range(n):
        item = locator.nth(i)
        # Title and URL
        title = ""
        url = ""
        try:
            if await item.locator("h2 a").count() > 0:
                link = item.locator("h2 a").first
            elif await item.locator("h3 a").count() > 0:
                link = item.locator("h3 a").first
            else:
                link = item.locator("a").first
            title = (await link.inner_text()).strip()
            href = await link.get_attribute("href")
            url = (href or "").strip()
        except Exception:  # pragma: no cover - DOM variability
            pass

        # Snippet
        snippet = ""
        try:
            for s in [
                "div.b_caption p",
                "p",
                ".b_snippet",
                "div[data-tag='snippet']",
            ]:
                if await item.locator(s).count() > 0:
                    snippet = (
                        await item.locator(s).first.inner_text()
                    ).strip()
                    break
        except Exception:  # pragma: no cover
            pass

        if title or url or snippet:
            results.append(Result(title=title, url=url, snippet=snippet))

    return results


async def search_bing(query: str) -> ToolResponse:
    """Search the public web on Bing (mobile web). Use only when the user
    explicitly asks to look up information online. Return brief public results;
    no analysis or summarization. Do not read/write files or alter metadata.
    """
    try:
        # Lazy import to avoid hard dependency at import time
        # pylint: disable=E0401
        from playwright.async_api import async_playwright  # type: ignore

        # pylint: enable=E0401

        async with async_playwright() as p:  # type: ignore
            browser = await p.webkit.launch(headless=True)
            context = await browser.new_context(
                user_agent=MOBILE_UA,
                viewport={"width": 375, "height": 812},
                is_mobile=True,
                locale="en-US",
            )
            page = await context.new_page()

            url = f"https://www.bing.com/search?q={quote_plus(query)}"
            await page.goto(url, wait_until="domcontentloaded")

            # Some pages lazy-load results; allow a short settle.
            try:
                await page.wait_for_selector("#b_results", timeout=2000)
            except Exception:
                await asyncio.sleep(0.2)

            rows = await _extract_results(page)
            rows = truncate_rows(rows, max_words=16384)
            text_out = render_text_list(rows)

            await context.close()
            await browser.close()
            return ToolResponse(
                content=[TextBlock(type="text", text=text_out)],
            )

    except Exception as e:  # pylint: disable=broad-except
        # Never mutate metadata; report error in content succinctly.
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "Error: failed to fetch Bing results "
                        f"({type(e).__name__}: {e})"
                    ),
                ),
            ],
        )


# Explicitly export only the tool function
__all__ = ["search_bing"]
