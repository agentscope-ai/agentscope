# -*- coding: utf-8 -*-
"""Tests for :class:`HtmlParser`."""

from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.rag._parser import HtmlParser


class HtmlParserTest(IsolatedAsyncioTestCase):
    """Test the HTML parser."""

    async def asyncSetUp(self) -> None:
        """Set up the parser for tests."""
        self.parser = HtmlParser()

    async def test_supported_types(self) -> None:
        """It advertises the correct media types and extensions."""
        self.assertIn("text/html", HtmlParser.supported_media_types)

        extensions = HtmlParser.supported_extensions()
        self.assertIn(".html", extensions)
        self.assertIn(".htm", extensions)

    async def test_parse_clean_html(self) -> None:
        """It correctly extracts text and ignores tags, scripts, and styles."""
        html_content = b"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>My Page</title>
            <style>body { color: red; }</style>
            <script>alert("Hello!");</script>
        </head>
        <body>
            <h1>Welcome to AgentScope</h1>
            <p>This is a <b>great</b> multi-agent framework.</p>
            <a href="https://example.com">Click here</a>
            <div>
                Some more text here.
            </div>
            <!-- This is a comment -->
        </body>
        </html>
        """

        sections = await self.parser.parse(html_content, "index.html")

        self.assertEqual(len(sections), 1)

        section = sections[0]
        self.assertEqual(section.source, "index.html")

        # Check that it extracted text but ignored script/style
        text = section.content.text
        self.assertNotIn("color: red;", text)
        self.assertNotIn("alert", text)

        # Check text was extracted properly
        self.assertIn("Welcome to AgentScope", text)
        self.assertIn("This is a great multi-agent framework.", text)
        self.assertIn("Click here", text)
        self.assertIn("Some more text here.", text)

        # Ensure single spaces
        self.assertNotIn("  ", text)

    async def test_parse_string(self) -> None:
        """It accepts pre-decoded string input."""
        html_string = "<html><body><p>Hello world!</p></body></html>"

        sections = await self.parser.parse(html_string, "string.html")

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].content.text, "Hello world!")

    async def test_invalid_input(self) -> None:
        """It raises TypeError on unsupported input."""
        with self.assertRaises(TypeError):
            await self.parser.parse(123, "invalid.html")  # type: ignore

    async def test_decode_error(self) -> None:
        """It raises ValueError on decode errors."""
        bad_bytes = b"\xff\xff\xff"
        with self.assertRaisesRegex(ValueError, "Failed to decode"):
            await self.parser.parse(bad_bytes, "bad.html")
