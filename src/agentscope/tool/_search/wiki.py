# -*- coding: utf-8 -*-
"""Wikipedia search via Action API (no browser required).

Docstring (tool description):
Use only when the user asks to search Wikipedia/the web for encyclopedic
entries. Input `query` (string). Return several public results (title, link,
short snippet) as plain text. No file writes and do not alter metadata. On
network errors, return a brief error message.
"""
from __future__ import annotations

import html
import re
from typing import List

import requests

from .._response import ToolResponse
from ...message import TextBlock
from .common import Result, truncate_rows, render_text_list


API_URL = "https://en.wikipedia.org/w/api.php"
UA = "AgentScope-Search/1.0 (+https://github.com/)"


def _strip_html(s: str) -> str:
    # Remove HTML tags and unescape entities
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return html.unescape(s)


def _pick_lang(query: str) -> str:
    # Simple heuristic: use zh for obvious CJK; default en
    if re.search(r"[\u4e00-\u9fff]", query):
        return "zh"
    return "en"


def search_wiki(query: str) -> ToolResponse:
    """Search Wikipedia using the public API and return plain text results."""
    lang = _pick_lang(query)
    if API_URL.startswith("https://en."):
        url = API_URL.replace("en.", f"{lang}.")
    else:
        url = f"https://{lang}.wikipedia.org/w/api.php"

    try:
        res = requests.get(
            url,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 10,
                "utf8": 1,
                "format": "json",
            },
            headers={"User-Agent": UA},
            timeout=8,
        )
        res.raise_for_status()
        data = res.json()
        items = (data.get("query", {}).get("search", []) or [])

        rows: List[Result] = []
        for it in items:
            title = str(it.get("title", ""))
            pageid = it.get("pageid")
            if pageid:
                link = f"https://{lang}.wikipedia.org/?curid={pageid}"
            else:
                link = ""
            snippet = _strip_html(str(it.get("snippet", "")))
            rows.append(Result(title=title, url=link, snippet=snippet))

        rows = truncate_rows(rows, max_words=5000)
        text = render_text_list(rows)
        return ToolResponse(content=[TextBlock(type="text", text=text)])

    except Exception as e:  # pylint: disable=broad-except
        return ToolResponse(
            content=[TextBlock(type="text", text=f"Error: {e}")],
        )
