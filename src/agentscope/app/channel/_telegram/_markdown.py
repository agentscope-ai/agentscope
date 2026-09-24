# -*- coding: utf-8 -*-
"""Safe Markdown rendering for Telegram's standard HTML parse mode."""
from __future__ import annotations

from dataclasses import dataclass
import html
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import urlsplit


_SAFE_LINK_SCHEMES = frozenset({"http", "https", "tg", "mailto"})
_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_+-]{1,32}$")


@dataclass(frozen=True)
class _TelegramTextChunk:
    """One independently sendable formatted text chunk."""

    html: str
    plain: str


@dataclass
class _ListState:
    """Rendering state for one nested Markdown list."""

    ordered: bool
    next_number: int = 1


def _safe_link(url: str) -> bool:
    """Return whether Telegram should receive ``url`` as a link."""
    try:
        return urlsplit(url).scheme.casefold() in _SAFE_LINK_SCHEMES
    except ValueError:
        return False


def _render_inline(children: list[Any]) -> str:
    """Render markdown-it inline tokens to Telegram-supported HTML."""
    rendered: list[str] = []
    link_stack: list[bool] = []
    tags = {
        "strong_open": "<b>",
        "strong_close": "</b>",
        "em_open": "<i>",
        "em_close": "</i>",
        "s_open": "<s>",
        "s_close": "</s>",
    }
    for token in children:
        token_type = token.type
        if token_type == "text":
            rendered.append(html.escape(token.content, quote=False))
        elif token_type in tags:
            rendered.append(tags[token_type])
        elif token_type == "code_inline":
            rendered.append(
                f"<code>{html.escape(token.content, quote=False)}</code>",
            )
        elif token_type in ("softbreak", "hardbreak"):
            rendered.append("\n")
        elif token_type == "link_open":
            href = str(token.attrGet("href") or "")
            allowed = _safe_link(href)
            link_stack.append(allowed)
            if allowed:
                rendered.append(f'<a href="{html.escape(href, quote=True)}">')
        elif token_type == "link_close":
            if link_stack and link_stack.pop():
                rendered.append("</a>")
        elif token_type == "image":
            label = token.content or "image"
            src = str(token.attrGet("src") or "")
            rendered.append(html.escape(label, quote=False))
            if _safe_link(src):
                rendered.append(
                    f' (<a href="{html.escape(src, quote=True)}">image</a>)',
                )
        elif token_type == "html_inline":
            rendered.append(html.escape(token.content, quote=False))
        elif token.content:
            rendered.append(html.escape(token.content, quote=False))
    while link_stack:
        if link_stack.pop():
            rendered.append("</a>")
    return "".join(rendered)


class _PlainTextParser(HTMLParser):
    """Extract visible text from HTML produced by this module."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str) -> str:
    parser = _PlainTextParser()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def _render_table(tokens: list[Any], start: int) -> tuple[str, int]:
    """Render a Markdown table as aligned, escaped preformatted text."""
    rows: list[list[str]] = []
    row: list[str] | None = None
    index = start + 1
    while index < len(tokens) and tokens[index].type != "table_close":
        token = tokens[index]
        if token.type == "tr_open":
            row = []
        elif token.type == "inline" and row is not None:
            row.append(_plain_text(_render_inline(token.children or [])))
        elif token.type == "tr_close" and row is not None:
            rows.append(row)
            row = None
        index += 1

    if not rows:
        return "", index
    columns = max(len(row_) for row_ in rows)
    widths = [0] * columns
    for row_ in rows:
        for column, cell in enumerate(row_):
            widths[column] = max(widths[column], len(cell))

    lines: list[str] = []
    for row_index, row_ in enumerate(rows):
        padded = [
            (row_[column] if column < len(row_) else "").ljust(
                widths[column],
            )
            for column in range(columns)
        ]
        lines.append(" | ".join(padded).rstrip())
        if row_index == 0 and len(rows) > 1:
            lines.append("-+-".join("-" * width for width in widths))
    table = "\n".join(lines)
    return f"<pre>{html.escape(table, quote=False)}</pre>", index


def _render_markdown(markdown: str) -> str:
    """Convert Markdown to the HTML subset accepted by Telegram."""
    # Imported only while a Telegram channel is running. AgentScope itself
    # remains importable without the optional ``channel`` dependencies.
    from markdown_it import MarkdownIt

    parser = (
        MarkdownIt(
            "commonmark",
            {"html": False, "linkify": False},
        )
        .enable("strikethrough")
        .enable("table")
    )
    tokens = parser.parse(markdown)
    rendered: list[str] = []
    lists: list[_ListState] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        token_type = token.type
        if token_type == "table_open":
            table, index = _render_table(tokens, index)
            if table:
                rendered.extend((table, "\n\n"))
        elif token_type == "heading_open":
            rendered.append("<b>")
        elif token_type == "heading_close":
            rendered.append("</b>\n\n")
        elif token_type == "paragraph_close":
            if not lists:
                rendered.append("\n\n")
        elif token_type == "blockquote_open":
            rendered.append("<blockquote>")
        elif token_type == "blockquote_close":
            rendered.append("</blockquote>\n\n")
        elif token_type == "bullet_list_open":
            lists.append(_ListState(ordered=False))
        elif token_type == "ordered_list_open":
            start = token.attrGet("start")
            lists.append(
                _ListState(
                    ordered=True,
                    next_number=int(start) if start is not None else 1,
                ),
            )
        elif token_type in ("bullet_list_close", "ordered_list_close"):
            if lists:
                lists.pop()
            if not lists:
                rendered.append("\n")
        elif token_type == "list_item_open":
            depth = max(0, len(lists) - 1)
            prefix = "• "
            if lists and lists[-1].ordered:
                prefix = f"{lists[-1].next_number}. "
                lists[-1].next_number += 1
            rendered.append(f"{'  ' * depth}{prefix}")
        elif token_type == "list_item_close":
            rendered.append("\n")
        elif token_type == "inline":
            rendered.append(_render_inline(token.children or []))
        elif token_type in ("fence", "code_block"):
            info = (token.info or "").strip().split(maxsplit=1)
            language = info[0] if info else ""
            language_attr = ""
            if language and _LANGUAGE_RE.fullmatch(language):
                language_attr = f' class="language-{language}"'
            rendered.append(
                f"<pre><code{language_attr}>"
                f"{html.escape(token.content.rstrip(chr(10)), quote=False)}"
                "</code></pre>\n\n",
            )
        elif token_type == "hr":
            rendered.append("────────\n\n")
        elif token_type == "html_block":
            rendered.append(
                f"{html.escape(token.content.rstrip(), quote=False)}\n\n",
            )
        index += 1
    return "".join(rendered).strip()


class _HTMLChunker(HTMLParser):
    """Split generated Telegram HTML while closing open tags per chunk."""

    _VOID_TAGS = frozenset({"br"})

    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=False)
        self.limit = limit
        self.stack: list[tuple[str, str]] = []
        self.html_parts: list[str] = []
        self.plain_parts: list[str] = []
        self.visible_length = 0
        self.chunks: list[_TelegramTextChunk] = []

    def _flush(self) -> None:
        if not self.plain_parts:
            return
        suffix = "".join(f"</{name}>" for name, _ in reversed(self.stack))
        self.chunks.append(
            _TelegramTextChunk(
                html="".join(self.html_parts) + suffix,
                plain="".join(self.plain_parts),
            ),
        )
        self.html_parts = [start for _, start in self.stack]
        self.plain_parts = []
        self.visible_length = 0

    def _append_text(self, value: str) -> None:
        remaining = value
        while remaining:
            available = self.limit - self.visible_length
            if available == 0:
                self._flush()
                available = self.limit
            piece = remaining[:available]
            remaining = remaining[len(piece) :]
            self.html_parts.append(html.escape(piece, quote=False))
            self.plain_parts.append(piece)
            self.visible_length += len(piece)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        start = self.get_starttag_text() or f"<{tag}>"
        self.html_parts.append(start)
        if tag not in self._VOID_TAGS:
            self.stack.append((tag, start))

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        self.html_parts.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        self.html_parts.append(f"</{tag}>")
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        self._append_text(data)

    def handle_entityref(self, name: str) -> None:
        self._append_text(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self._append_text(html.unescape(f"&#{name};"))

    def finish(self) -> list[_TelegramTextChunk]:
        """Close the parser and return every completed text chunk."""
        self.close()
        self._flush()
        return self.chunks


def _telegram_markdown_chunks(
    markdown: str,
    limit: int,
) -> list[_TelegramTextChunk]:
    """Render Markdown into valid Telegram HTML/plain-text chunk pairs."""
    if not markdown:
        return []
    rendered = _render_markdown(markdown)
    if not rendered:
        return []
    chunker = _HTMLChunker(limit)
    chunker.feed(rendered)
    return chunker.finish()


__all__ = ["_TelegramTextChunk", "_telegram_markdown_chunks"]
