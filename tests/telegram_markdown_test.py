# -*- coding: utf-8 -*-
"""Tests for Telegram's safe Markdown-to-HTML renderer."""
# pylint: disable=protected-access,missing-function-docstring
import importlib.util
from html.parser import HTMLParser
from unittest import TestCase

import pytest

from agentscope.app.channel._telegram._markdown import (
    _plain_text,
    _render_markdown,
    _telegram_markdown_chunks,
)

if importlib.util.find_spec("markdown_it") is None:
    pytest.skip(
        "Telegram Markdown tests require agentscope[channel]",
        allow_module_level=True,
    )


class _TagValidator(HTMLParser):
    """Assert that every independently sendable chunk has balanced tags."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            raise AssertionError(f"unbalanced closing tag: {tag}")


class TelegramMarkdownTest(TestCase):
    """Cover supported formatting, safety, tables, and chunking."""

    def test_supported_markdown_maps_to_telegram_html(self) -> None:
        rendered = _render_markdown(
            "# Heading\n\n"
            "**bold** *italic* ~~gone~~ `inline`\n\n"
            "> quote\n\n"
            "- one\n- two\n\n"
            "1. first\n2. second\n\n"
            "[safe](https://example.test/a?x=1&y=2)\n\n"
            "```python\nprint('<tag>')\n```",
        )

        self.assertIn("<b>Heading</b>", rendered)
        self.assertIn("<b>bold</b>", rendered)
        self.assertIn("<i>italic</i>", rendered)
        self.assertIn("<s>gone</s>", rendered)
        self.assertIn("<code>inline</code>", rendered)
        self.assertIn("<blockquote>quote", rendered)
        self.assertIn("• one", rendered)
        self.assertIn("1. first", rendered)
        self.assertIn(
            '<a href="https://example.test/a?x=1&amp;y=2">safe</a>',
            rendered,
        )
        self.assertIn('<pre><code class="language-python">', rendered)
        self.assertIn("&lt;tag&gt;", rendered)

    def test_raw_html_and_unsafe_links_are_not_interpreted(self) -> None:
        rendered = _render_markdown(
            "<b>raw</b>\n\n"
            "[unsafe](javascript:alert(1)) "
            "[relative](/admin) "
            "[telegram](tg://user?id=123)",
        )

        self.assertIn("&lt;b&gt;raw&lt;/b&gt;", rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertNotIn('href="/admin', rendered)
        self.assertIn('href="tg://user?id=123"', rendered)

    def test_table_degrades_to_preformatted_text(self) -> None:
        rendered = _render_markdown(
            "| name | value |\n"
            "| --- | --- |\n"
            "| alpha | 1 |\n"
            "| beta | 20 |",
        )

        self.assertTrue(rendered.startswith("<pre>"))
        self.assertTrue(rendered.endswith("</pre>"))
        self.assertIn("name  | value", _plain_text(rendered))
        self.assertIn("alpha | 1", _plain_text(rendered))

    def test_unlabelled_code_fence_is_safe_while_streaming(self) -> None:
        for markdown in (
            "```",
            "```\nprint('<partial>')",
            "```\nprint('<complete>')\n```",
        ):
            with self.subTest(markdown=markdown):
                rendered = _render_markdown(markdown)
                self.assertIn("<pre><code>", rendered)
                self.assertNotIn('class="language-', rendered)
                self.assertNotIn("<partial>", rendered)
                self.assertNotIn("<complete>", rendered)

    def test_long_formatted_text_has_independent_valid_chunks(self) -> None:
        chunks = _telegram_markdown_chunks(
            f"[**{'x' * 8200}**](https://example.test)",
            4096,
        )

        self.assertEqual(len(chunks), 3)
        self.assertEqual("".join(chunk.plain for chunk in chunks), "x" * 8200)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.plain), 4096)
            validator = _TagValidator()
            validator.feed(chunk.html)
            validator.close()
            self.assertEqual(validator.stack, [])
            self.assertTrue(chunk.html.startswith('<a href="https://'))
            self.assertTrue(chunk.html.endswith("</a>"))

    def test_empty_markdown_produces_no_messages(self) -> None:
        self.assertEqual(_telegram_markdown_chunks("", 4096), [])
