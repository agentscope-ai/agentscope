# -*- coding: utf-8 -*-
"""E2E: use local Chrome to fetch Google results in one page.

Strategy
- Try CDP to an existing Chrome at :9222; if not available, launch system Chrome channel.
- Same page: goto /search?q=...&hl=en&gl=us&pws=0&udm=14, click consent if shown, extract results.
- No storage state persistence; real output is printed.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import socket

import pytest


def _is_port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def test_google_local_chrome_e2e() -> None:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception as e:  # pragma: no cover
        pytest.skip(f"playwright not available: {e}")

    from agentscope.tool import search_google, ToolResponse  # type: ignore

    async def _run() -> None:
        async with async_playwright() as p:  # type: ignore
            browser = None
            # 1) Prefer existing local Chrome via CDP port 9222
            if _is_port_open("127.0.0.1", 9222):
                try:
                    browser = await p.chromium.connect_over_cdp("http://localhost:9222")  # type: ignore
                except Exception:
                    browser = None

            # 2) Fallback: launch system Chrome channel
            if browser is None:
                try:
                    browser = await p.chromium.launch(channel="chrome", headless=False)  # type: ignore
                except Exception as e:
                    pytest.skip(f"cannot launch local Chrome channel: {e}")

            try:
                query = "who is speed"
                res: ToolResponse = await search_google(query, client=browser)  # type: ignore
                blocks = [b for b in res.content if b.get("type") == "text"]
                assert blocks, "no text blocks in ToolResponse"
                text = "\n".join(b.get("text", "") for b in blocks)
                print("=== E2E Google Local Chrome (who is speed) ===")
                print(text)
                print("=== END ===")
                assert re.search(r"https?://\S+", text) is not None, "no URL detected in output"
            finally:
                with contextlib.suppress(Exception):
                    await browser.close()  # type: ignore

    asyncio.run(_run())

