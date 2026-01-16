# -*- coding: utf-8 -*-
"""Internal helpers for search tools (not registered as tools).

This module contains pure functions and types shared by search providers.
It MUST NOT register any tool or alter public JSON Schemas.
"""
from __future__ import annotations

from typing import TypedDict, List


class Result(TypedDict):
    """A single search result item."""

    title: str
    url: str
    snippet: str


def _normalize_whitespace(text: str) -> str:
    return " ".join((text or "").split())


def truncate_rows(rows: List[Result], max_words: int = 5000) -> List[Result]:
    """Truncate total word budget across snippets to keep output bounded."""
    if max_words <= 0:
        return rows
    budget = max_words
    out: List[Result] = []
    for r in rows:
        words = r.get("snippet", "").split()
        if not words:
            out.append(r)
            continue
        if len(words) <= budget:
            out.append(r)
            budget -= len(words)
        else:
            cut = max(1, budget)
            snippet = " ".join(words[:cut]) + (
                " …" if len(words) > cut else ""
            )
            out.append(Result(title=r["title"], url=r["url"], snippet=snippet))
            budget = 0
        if budget <= 0:
            break
    return out


def render_text_list(rows: List[Result]) -> str:
    """Render results as plain text (no Markdown), one item per block.

    Format per item:
    - title: <title>
      url: <url>
      snippet: <snippet>
    """
    lines: List[str] = []
    for r in rows:
        title = _normalize_whitespace(r.get("title", ""))
        url = r.get("url", "")
        snippet = _normalize_whitespace(r.get("snippet", ""))
        if title or url or snippet:
            lines.append(f"- title: {title or url}")
            lines.append(f"  url: {url}")
            if snippet:
                lines.append(f"  snippet: {snippet}")
    return "\n".join(lines) if lines else "<no results>"
