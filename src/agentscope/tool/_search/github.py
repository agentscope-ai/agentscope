# -*- coding: utf-8 -*-
"""GitHub repository search with README excerpt (mobile web).

Note: the function docstring on `search_github` is used as the tool
description when registering the tool; keep it concise and model-facing.
"""
from __future__ import annotations

import asyncio
from typing import List, Tuple
from urllib.parse import quote_plus

from .._response import ToolResponse
from ...message import TextBlock
from .common import Result, truncate_rows


MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/16.0 Mobile/15E148 Safari/604.1"
)


# type: ignore[no-untyped-def]
async def _extract_repo_list(page) -> List[Tuple[str, str, str]]:
    """Return list of (title, url, desc) from GitHub search results.

    Implements a two-step strategy:
    1) Try structured cards (desktop/mobile).
    2) Fallback to scanning anchors matching https://github.com/<owner>/<repo>.
    """
    repos: List[Tuple[str, str, str]] = []

    # Wait for some container on search page
    try:
        await page.wait_for_selector(
            "a.v-align-middle, a.Link--primary, h3 a",
            timeout=1500,
        )
    except Exception:
        pass

    # Candidate item containers
    item_selectors = [
        "ul.repo-list li",
        "div.search-title",
        "div.search-match",  # description hint
        "main .container-lg div.f4",
        "main .codesearch-results div.f4",
    ]

    # Choose first selector with hits
    locator = None
    for sel in item_selectors:
        loc = page.locator(sel)
        if await loc.count() > 0:
            locator = loc
            break
    if locator is None:
        # Fallback: scan all anchors and pick repo-like links
        try:
            await page.wait_for_selector(
                'a[href^="https://github.com/"]',
                timeout=3000,
            )
        except Exception:
            pass
        anchors = page.locator('a[href^="https://github.com/"]')
        seen: set[str] = set()
        count = await anchors.count()
        for i in range(min(count, 50)):
            a = anchors.nth(i)
            href = (await a.get_attribute("href") or "").strip()
            # Expect paths like /owner/repo
            try:
                parts = href.split("/")
                # ['https:', '', 'github.com', owner, repo, ...]
                if len(parts) >= 5:
                    owner = parts[3]
                    repo = parts[4]
                    blocked = [
                        "issues",
                        "pull",
                        "blob",
                        "search",
                        "sponsors",
                        "topics",
                    ]
                    if owner and repo and all(x not in repo for x in blocked):
                        key = f"{owner}/{repo}"
                        if key not in seen:
                            seen.add(key)
                            title = f"{owner}/{repo}"
                            repos.append((title, href, ""))
                            if len(repos) >= 10:
                                break
            except Exception:  # pragma: no cover
                continue
        return repos

    n = min(await locator.count(), 10)
    for i in range(n):
        item = locator.nth(i)
        title = ""
        url = ""
        desc = ""
        try:
            link = None
            for sel in ["a.v-align-middle", "a.Link--primary", "h3 a", "a"]:
                if await item.locator(sel).count() > 0:
                    link = item.locator(sel).first
                    break
            if link is not None:
                title = (await link.inner_text()).strip()
                href = (await link.get_attribute("href") or "").strip()
                if href.startswith("http"):
                    url = href
                else:
                    url = f"https://github.com{href}"
        except Exception:  # pragma: no cover
            pass

        # Description fallback from card
        try:
            for s in ["p.mb-1", ".search-match", ".color-fg-muted"]:
                if await item.locator(s).count() > 0:
                    desc = (await item.locator(s).first.inner_text()).strip()
                    break
        except Exception:  # pragma: no cover
            pass

        if title or url:
            repos.append((title, url, desc))

    return repos


async def _extract_readme(page) -> str:  # type: ignore[no-untyped-def]
    """Extract a short README excerpt from a repo page (plain text)."""
    selectors = [
        "#readme .Box-body",
        "article.markdown-body",
        "div#readme",
        "div.BorderGrid-cell .markdown-body",
    ]
    for sel in selectors:
        try:
            if await page.locator(sel).count() > 0:
                text = (await page.locator(sel).first.inner_text()).strip()
                if text:
                    return text
        except Exception:  # pragma: no cover
            pass
    return ""


def _render_plain(results: List[Result]) -> str:
    lines: List[str] = []
    for r in results:
        lines.append(f"- title: {r['title']}")
        lines.append(f"  url: {r['url']}")
        if r["snippet"]:
            lines.append(f"  snippet: {r['snippet']}")
    return "\n".join(lines) if lines else "<no results>"


async def search_github(query: str) -> ToolResponse:
    """Search for GitHub repositories and return a few results with a README
    excerpt (if available). Uses public web search with a GitHub domain filter
    to avoid API/keys. Use only when the user explicitly asks to look up
    information online. No analysis; do not read/write files or alter metadata.
    """
    try:
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

            # Use Sogou with a GitHub site filter to retrieve repo links
            url = (
                "https://www.sogou.com/web?query=site%3Agithub.com+"
                f"{quote_plus(query)}"
            )
            await page.goto(url, wait_until="domcontentloaded")
            # Extract GitHub repo anchors from Sogou results
            try:
                await page.wait_for_selector("h3 a, h4 a, #main", timeout=3000)
            except Exception:
                await asyncio.sleep(0.2)
            anchors = page.locator("a")
            seen: set[str] = set()
            repos: List[Tuple[str, str, str]] = []
            count = await anchors.count()
            for i in range(min(count, 100)):
                a = anchors.nth(i)
                href = (await a.get_attribute("href") or "").strip()
                # Normalize Sogou redirect/relative links to absolute
                # destination.
                if href.startswith("/web?") or href.startswith("./"):
                    # try to extract url= param
                    try:
                        from urllib.parse import parse_qs
                        q = href.split("?", 1)[1] if "?" in href else ""
                        params = parse_qs(q)
                        cand = params.get("url", [])
                        if cand:
                            href = cand[0]
                    except Exception:
                        pass
                if not href.startswith("https://github.com/"):
                    continue
                parts = href.split("/")
                if len(parts) >= 5:
                    owner, repo = parts[3], parts[4]
                    blocked = [
                        "issues",
                        "pull",
                        "blob",
                        "search",
                        "sponsors",
                        "topics",
                    ]
                    if owner and repo and all(x not in repo for x in blocked):
                        key = f"{owner}/{repo}"
                        if key in seen:
                            continue
                        seen.add(key)
                        title = key
                        repos.append((title, href, ""))
                        if len(repos) >= 5:
                            break

            out: List[Result] = []
            for title, repo_url, desc in repos:
                snippet = ""
                try:
                    await page.goto(repo_url, wait_until="domcontentloaded")
                    # small settle time for README render
                    try:
                        await page.wait_for_selector(
                            "#readme, article.markdown-body",
                            timeout=2000,
                        )
                    except Exception:
                        await asyncio.sleep(0.2)
                    snippet = await _extract_readme(page)
                except Exception:  # pragma: no cover
                    snippet = ""
                if not snippet:
                    snippet = desc or ""
                out.append(Result(title=title, url=repo_url, snippet=snippet))

            out = truncate_rows(out, max_words=5000)
            text_out = _render_plain(out)

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
                        "Error: failed to fetch GitHub results "
                        f"({type(e).__name__}: {e})"
                    ),
                ),
            ],
        )


__all__ = ["search_github"]
