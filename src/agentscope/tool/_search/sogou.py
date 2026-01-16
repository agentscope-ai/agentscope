# -*- coding: utf-8 -*-
"""Sogou mobile web search (implementation file).

Note: the function docstring on `search_sogou` is used as the tool description
when registering the tool; keep that one concise and model-facing.
"""
from __future__ import annotations

import asyncio
from typing import List
from urllib.parse import quote_plus, parse_qs, unquote

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
    """Best-effort extraction for Sogou results on mobile/desktop DOM."""
    results: List[Result] = []

    # Wait for some generic anchors inside result blocks
    await page.wait_for_selector("h3 a, h4 a, #main, .results")

    # Candidate result item selectors (Sogou varies across AB tests)
    item_selectors = [
        "div.vrwrap",           # common vr result wrapper
        "div.vrResult",         # alternative wrapper
        "div.results > div",    # generic results list
        "#main .vrwrap",
        "#main .vrResult",
        "#main > div",
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
        title = ""
        url = ""
        try:
            if await item.locator("h3 a").count() > 0:
                link = item.locator("h3 a").first
            elif await item.locator("h4 a").count() > 0:
                link = item.locator("h4 a").first
            else:
                link = item.locator("a").first
            title = (await link.inner_text()).strip()
            href = await link.get_attribute("href")
            raw = (href or "").strip()
            # Normalize Sogou redirect/relative links to absolute destination
            if raw.startswith("http://") or raw.startswith("https://"):
                url = raw
            else:
                # Attempt to extract real target from query param `url=`
                try:
                    qpos = raw.find("?")
                    query_str = raw[qpos + 1 :] if qpos >= 0 else ""
                    q = parse_qs(query_str)
                    cand = q.get("url", [])
                    if cand:
                        url = unquote(cand[0])
                    else:
                        # Fallback to absolute path on Sogou domain
                        url = "https://www.sogou.com" + (
                            raw[1:] if raw.startswith("./") else raw
                        )
                except Exception:  # pragma: no cover
                    url = raw
        except Exception:  # pragma: no cover
            pass

        snippet = ""
        try:
            for s in [
                "p",                # primary snippet in many layouts
                ".ft",              # fallback footer text
                ".str_info",        # structured info
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


async def search_sogou(query: str) -> ToolResponse:
    """Search the public web on Sogou (mobile web). Use only when the user
    explicitly asks to look up information online. Return brief public results;
    no analysis or summarization. Do not read/write files or alter metadata.
    """
    try:
        from playwright.async_api import async_playwright  # type: ignore

        async with async_playwright() as p:  # type: ignore
            browser = await p.webkit.launch(headless=True)
            context = await browser.new_context(
                user_agent=MOBILE_UA,
                viewport={"width": 375, "height": 812},
                is_mobile=True,
                locale="zh-CN",
            )
            page = await context.new_page()

            url = f"https://www.sogou.com/web?query={quote_plus(query)}"
            await page.goto(url, wait_until="domcontentloaded")

            try:
                await page.wait_for_selector("h3 a, h4 a, #main", timeout=2000)
            except Exception:
                await asyncio.sleep(0.2)

            rows = await _extract_results(page)
            rows = truncate_rows(rows, max_words=5000)
            text_out = render_text_list(rows)

            await context.close()
            await browser.close()
            return ToolResponse(
                content=[TextBlock(type="text", text=text_out)],
            )

    except Exception as e:  # pylint: disable=broad-except
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "Error: failed to fetch Sogou results "
                        f"({type(e).__name__}: {e})"
                    ),
                ),
            ],
        )


__all__ = ["search_sogou"]
