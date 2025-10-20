# -*- coding: utf-8 -*-
"""Google mobile web search (implementation file).

Note: the function docstring on `search_google` is used as the tool description
when registering；保持简洁且面向模型的说明。
"""
from __future__ import annotations

import asyncio
from typing import Any, List
from urllib.parse import quote_plus

from .._response import ToolResponse
from ...message import TextBlock
from .common import Result, truncate_rows, render_text_list


MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)


async def _extract_results(page) -> List[Result]:  # type: ignore[no-untyped-def]
    """Best-effort extraction for Google results on mobile/desktop DOM.

    选择器会根据实际页面逐步校准；当前采用常见容器与字段选择器。
    """
    results: List[Result] = []

    # 等待主结果容器；若超时不抛出，走空集合回退
    # Prefer robust anchor-with-heading locator; fall back to classic containers
    anchor_locator = page.locator("a:has(h3)")
    if await anchor_locator.count() == 0:
        try:
            await page.wait_for_selector("div#search, div.g, div.MjjYud", timeout=8000)
        except Exception:
            pass
        # fallback gather
        anchor_locator = page.locator("div#search a:has(h3), div.g a:has(h3), div.MjjYud a:has(h3)")

    n = min(await anchor_locator.count(), 10)
    for i in range(n):
        a = anchor_locator.nth(i)
        try:
            title = (await a.locator("h3").first.inner_text()).strip()
        except Exception:
            title = ""
        try:
            href = await a.get_attribute("href")
            url = (href or "").strip()
        except Exception:
            url = ""

        snippet = ""
        # Try to fetch a nearby snippet block relative to the anchor
        for sel in [
            "xpath=ancestor::div[contains(@class,'g')]//div[contains(@class,'VwiC3b')]",
            "xpath=ancestor::div[contains(@class,'MjjYud')]//div[contains(@class,'VwiC3b')]",
            "xpath=following::div[contains(@class,'VwiC3b')][1]",
        ]:
            try:
                cand = a.locator(sel)
                if await cand.count():
                    snippet = (await cand.first.inner_text()).strip()
                    if snippet:
                        break
            except Exception:
                continue

        if title or url or snippet:
            results.append(Result(title=title, url=url, snippet=snippet))

    return results


async def search_google(query: str, *, client: Any) -> ToolResponse:
    """当且仅当用户请求“用谷歌/Google 在线搜索”时调用；输入 `query`，
    返回若干条公开网页结果（标题、链接与简短摘要文本）。不进行文件写入或
    修改元数据；网络异常时返回简短错误提示。
    """
    try:
        context = await client.new_context(
            user_agent=MOBILE_UA,
            viewport={"width": 375, "height": 812},
            is_mobile=True,
            locale="en-US",
        )
        page = await context.new_page()
        # 单一路径：标准 Google 搜索页（英文、美国、禁个性化、简化版）
        search_url = (
            f"https://www.google.com/search?q={quote_plus(query)}&hl=en&gl=us&pws=0&udm=14"
        )
        await page.goto(search_url, wait_until="domcontentloaded")
        await _try_accept_consent(page)
        # 二次等待以确保同意后渲染
        try:
            await page.wait_for_selector("a:has(h3), div#search", timeout=5000)
        except Exception:
            pass
        rows = await _extract_results(page)

        rows = truncate_rows(rows, max_words=5000)
        text = render_text_list(rows)

        await context.close()
        return ToolResponse(content=[TextBlock(type="text", text=text)])

    except Exception as e:  # pylint: disable=broad-except
        return ToolResponse(
            content=[TextBlock(type="text", text=f"Error: failed to fetch Google results ({type(e).__name__}: {e})")],
        )


async def _try_accept_consent(page) -> None:  # type: ignore[no-untyped-def]
    """Best-effort click on consent dialogs (page or iframes)."""
    selectors = [
        "#L2AGLb",
        "button:has-text('Accept all')",
        "button:has-text('I agree')",
        "button:has-text('同意')",
        "button:has-text('接受')",
        "button:has-text('同意全部')",
        "button:has-text('同意所有')",
    ]
    # try on main page
    for sel in selectors:
        try:
            btn = page.locator(sel)
            if await btn.count():
                await btn.first.click(timeout=1500)
                return
        except Exception:
            continue
    # try in iframes
    try:
        for frame in page.frames:
            for sel in selectors:
                try:
                    btn = frame.locator(sel)
                    if await btn.count():
                        await btn.first.click(timeout=1500)
                        return
                except Exception:
                    continue
    except Exception:
        pass
