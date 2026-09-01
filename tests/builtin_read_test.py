# -*- coding: utf-8 -*-
"""Read tool test case."""
import base64
import io
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase
from utils import AnyString

from agentscope.tool import ToolChunk, Read
from agentscope.permission import (
    PermissionContext,
    PermissionBehavior,
    PermissionRule,
)
from agentscope.message import TextBlock


# pylint: disable=too-many-public-methods
class ReadToolTest(IsolatedAsyncioTestCase):
    """The read tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.read_tool = Read()
        # Create a temporary file for testing
        self.temp_file = (
            tempfile.NamedTemporaryFile(  # pylint: disable=consider-using-with
                mode="w",
                delete=False,
                suffix=".txt",
            )
        )
        # Write multiple lines
        for i in range(1, 11):
            self.temp_file.write(f"Line {i}\n")
        self.temp_file.close()

    async def asyncTearDown(self) -> None:
        """Clean up temporary files."""
        if os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)

    async def test_tool_properties(self) -> None:
        """Test read tool properties."""
        self.assertEqual(self.read_tool.name, "Read")
        self.assertDictEqual(
            self.read_tool.input_schema,
            {
                "type": "object",
                "description": "The parameters of the Read tool.",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The absolute path to the file to "
                        "read.",
                    },
                    "offset": {
                        "type": "integer",
                        "default": 1,
                        "minimum": 1,
                        "description": "Optional 1-based line number to "
                        "start reading from. Only applies to plain text "
                        "files (default: 1)",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 2000,
                        "minimum": 1,
                        "maximum": 2000,
                        "description": "Optional maximum number of lines to "
                        "read. Only applies to plain text files (default: "
                        "2000, max: 2000)",
                    },
                    "pages": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "description": 'Page range for PDF files (e.g. "1-5", '
                        '"3", "10-20"), max 20 pages per request; required '
                        "for PDFs over 10 pages. Only applies to PDF files.",
                    },
                },
                "required": ["file_path"],
            },
        )
        # The image and PDF bullets follow the model's input types.
        self.assertEqual(
            self.read_tool.description.splitlines()[-2:],
            [
                "- This tool allows you to read images (image/png, "
                "image/jpeg, image/gif, image/webp). When reading an image "
                "file the contents are presented visually as you're a "
                "multimodal LLM.",
                "- This tool can read PDF files (.pdf). Text is extracted "
                "per page. For large PDFs (more than 10 pages), you MUST "
                "provide the pages parameter to read specific pages (max "
                "20 pages per request).",
            ],
        )
        self.assertEqual(
            Read(
                model_input_types=["text/plain", "application/pdf"],
            ).description.splitlines()[-2:],
            [
                "- Results are returned using cat -n format, with line "
                "numbers starting at 1",
                "- This tool can read PDF files (.pdf). When reading a PDF "
                "file the pages are presented to you as a document. For "
                "large PDFs (more than 10 pages), you MUST provide the "
                "pages parameter to read specific pages (max 20 pages per "
                "request).",
            ],
        )
        self.assertFalse(self.read_tool.is_mcp)
        self.assertTrue(self.read_tool.is_read_only)
        self.assertTrue(self.read_tool.is_concurrency_safe)

    async def test_check_permissions(self) -> None:
        """Test read tool permission checking."""
        context = PermissionContext()
        tool_input = {"file_path": "/tmp/test.txt"}
        decision = await self.read_tool.check_permissions(tool_input, context)

        # Read/Glob/Grep are read-only, return PASSTHROUGH
        self.assertEqual(decision.behavior, PermissionBehavior.PASSTHROUGH)

    async def test_simple_read(self) -> None:
        """Test simple file reading."""
        chunk = await self.read_tool(file_path=self.temp_file.name)

        self.assertIsInstance(chunk, ToolChunk)
        self.assertEqual(chunk.state, "running")
        self.assertEqual(len(chunk.content), 1)
        self.assertIsInstance(chunk.content[0], TextBlock)

        content = chunk.content[0].text
        # Should contain all lines with line numbers
        self.assertIn("Line 1", content)
        self.assertIn("Line 10", content)

    async def test_read_with_offset(self) -> None:
        """Test reading with offset."""
        chunk = await self.read_tool(
            file_path=self.temp_file.name,
            offset=5,
        )

        self.assertEqual(chunk.state, "running")
        content = chunk.content[0].text

        # Should start from line 5
        self.assertIn("Line 5", content)
        # Line 1 should not appear (but Line 10 contains "1",
        # so check more specifically)
        lines = content.split("\n")
        line_numbers = [
            int(line.split("\t")[0].strip()) for line in lines if line.strip()
        ]
        self.assertNotIn(1, line_numbers)
        self.assertIn(5, line_numbers)

    async def test_read_with_limit(self) -> None:
        """Test reading with limit."""
        chunk = await self.read_tool(
            file_path=self.temp_file.name,
            offset=1,
            limit=3,
        )

        self.assertEqual(chunk.state, "running")
        content = chunk.content[0].text

        # Should only read 3 lines
        self.assertIn("Line 1", content)
        self.assertIn("Line 2", content)
        self.assertIn("Line 3", content)
        self.assertNotIn("Line 4", content)

    async def test_read_nonexistent_file(self) -> None:
        """Test reading a non-existent file."""
        chunk = await self.read_tool(file_path="/nonexistent/file.txt")

        self.assertEqual(chunk.state, "error")
        self.assertIn("does not exist", chunk.content[0].text)

    async def test_read_directory(self) -> None:
        """Test reading a directory (should fail)."""
        temp_dir = tempfile.mkdtemp()
        try:
            chunk = await self.read_tool(file_path=temp_dir)

            self.assertEqual(chunk.state, "error")
            self.assertIn("directory", chunk.content[0].text.lower())
        finally:
            os.rmdir(temp_dir)

    async def test_match_rule_glob_pattern(self) -> None:
        """Test match_rule with glob patterns."""
        # Test exact match
        self.assertTrue(
            await self.read_tool.match_rule(
                "test.py",
                {"file_path": "test.py"},
            ),
        )

        # Test wildcard pattern
        self.assertTrue(
            await self.read_tool.match_rule(
                "*.py",
                {"file_path": "test.py"},
            ),
        )

        # Test directory pattern
        self.assertTrue(
            await self.read_tool.match_rule(
                "/tmp/**",
                {"file_path": "/tmp/test.py"},
            ),
        )

        # Test non-matching pattern
        self.assertFalse(
            await self.read_tool.match_rule(
                "*.txt",
                {"file_path": "test.py"},
            ),
        )

        # Test empty file_path
        self.assertFalse(
            await self.read_tool.match_rule(
                "*.py",
                {"file_path": ""},
            ),
        )

    async def test_generate_suggestions(self) -> None:
        """Test generate_suggestions for file operations."""

        # Test suggestion for file in subdirectory
        suggestions = await self.read_tool.generate_suggestions(
            {"file_path": "/tmp/project/src/main.py"},
        )

        self.assertIsInstance(suggestions, list)
        self.assertGreater(len(suggestions), 0)
        self.assertIsInstance(suggestions[0], PermissionRule)

        # Should suggest parent directory pattern
        suggestion_contents = [s.rule_content for s in suggestions]
        self.assertIn("/tmp/project/src/**", suggestion_contents)

        # Test suggestion for file in root
        suggestions = await self.read_tool.generate_suggestions(
            {"file_path": "/test.py"},
        )
        self.assertGreater(len(suggestions), 0)

    async def test_read_image_file_returns_data_block(self) -> None:
        """Test reading an image file returns a base64 DataBlock."""
        img_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
            f.write(img_data)
        self.addCleanup(os.unlink, f.name)

        chunk = await self.read_tool(file_path=f.name)
        self.assertDictEqual(
            chunk.model_dump(mode="json"),
            {
                "content": [
                    {
                        "type": "data",
                        "id": AnyString(),
                        "source": {
                            "type": "base64",
                            "data": base64.b64encode(img_data).decode(),
                            "media_type": "image/png",
                        },
                        "name": os.path.basename(f.name),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_read_image_unsupported_type(self) -> None:
        """Test images outside ``model_input_types`` return an error."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bmp") as f:
            f.write(b"BM" + b"\x00" * 100)
        self.addCleanup(os.unlink, f.name)

        # image/bmp is not in the default input types.
        chunk = await self.read_tool(file_path=f.name)
        self.assertDictEqual(
            chunk.model_dump(mode="json"),
            {
                "content": [
                    {
                        "type": "text",
                        "text": "Error: Unsupported image type image/bmp, "
                        "only image/png, image/jpeg, image/gif, image/webp "
                        "are supported.",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "error",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Model card style input types (non-image entries ignored) and glob
        # patterns are accepted.
        for model_input_types in [["text/plain", "image/bmp"], ["image/*"]]:
            tool = Read(model_input_types=model_input_types)
            chunk = await tool(file_path=f.name)
            self.assertDictEqual(
                chunk.model_dump(mode="json"),
                {
                    "content": [
                        {
                            "type": "data",
                            "id": AnyString(),
                            "source": {
                                "type": "base64",
                                "data": base64.b64encode(
                                    b"BM" + b"\x00" * 100,
                                ).decode(),
                                "media_type": "image/bmp",
                            },
                            "name": os.path.basename(f.name),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "state": "running",
                    "is_last": True,
                    "metadata": {},
                    "id": AnyString(),
                },
            )

        # The attribute can be changed after construction.
        tool.model_input_types = ["text/plain"]
        chunk = await tool(file_path=f.name)
        self.assertDictEqual(
            chunk.model_dump(mode="json"),
            {
                "content": [
                    {
                        "type": "text",
                        "text": "Error: Unsupported image type image/bmp, "
                        "only none are supported.",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "error",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )
        self.assertNotIn("read images", tool.description)

    async def test_read_pdf_file(self) -> None:
        """Test reading a PDF file extracts text per page."""
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=612, height=792)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            writer.write(f)
        self.addCleanup(os.unlink, f.name)

        chunk = await self.read_tool(file_path=f.name)
        self.assertDictEqual(
            chunk.model_dump(mode="json"),
            {
                "content": [
                    {
                        "type": "text",
                        "text": "--- Page 1/2 ---\n\n\n--- Page 2/2 ---\n",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_read_pdf_with_pages_param(self) -> None:
        """Test reading a page range and a single page from a PDF."""
        from pypdf import PdfWriter

        writer = PdfWriter()
        for _ in range(5):
            writer.add_blank_page(width=612, height=792)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            writer.write(f)
        self.addCleanup(os.unlink, f.name)

        # A range, a single page, and a range clipped to the last page.
        for pages, text in [
            ("2-3", "--- Page 2/5 ---\n\n\n--- Page 3/5 ---\n"),
            ("4", "--- Page 4/5 ---\n"),
            ("4-99", "--- Page 4/5 ---\n\n\n--- Page 5/5 ---\n"),
        ]:
            chunk = await self.read_tool(file_path=f.name, pages=pages)
            self.assertDictEqual(
                chunk.model_dump(mode="json"),
                {
                    "content": [
                        {
                            "type": "text",
                            "text": text,
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "state": "running",
                    "is_last": True,
                    "metadata": {},
                    "id": AnyString(),
                },
                pages,
            )

    async def test_read_pdf_invalid_pages(self) -> None:
        """Test malformed or out-of-range pages return an error."""
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=612, height=792)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            writer.write(f)
        self.addCleanup(os.unlink, f.name)

        for pages, text in [
            (
                "abc",
                "Error: Invalid pages 'abc'. Expected a page number or "
                'range like "3" or "1-5".',
            ),
            (
                "1-2-3",
                "Error: Invalid pages '1-2-3'. Expected a page number or "
                'range like "3" or "1-5".',
            ),
            ("0", "Error: Invalid pages '0'. PDF has 2 page(s)."),
            ("3", "Error: Invalid pages '3'. PDF has 2 page(s)."),
            ("2-1", "Error: Invalid pages '2-1'. PDF has 2 page(s)."),
        ]:
            chunk = await self.read_tool(file_path=f.name, pages=pages)
            self.assertDictEqual(
                chunk.model_dump(mode="json"),
                {
                    "content": [
                        {
                            "type": "text",
                            "text": text,
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "state": "error",
                    "is_last": True,
                    "metadata": {},
                    "id": AnyString(),
                },
                pages,
            )

    async def test_read_large_pdf_page_limits(self) -> None:
        """Test PDFs over 10 pages need ``pages`` and reads cap at 20."""
        from pypdf import PdfWriter

        writer = PdfWriter()
        for _ in range(25):
            writer.add_blank_page(width=612, height=792)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            writer.write(f)
        self.addCleanup(os.unlink, f.name)

        for pages, text in [
            (
                None,
                "Error: PDF has 25 pages, more than 10. You must provide "
                'the pages parameter (e.g. "1-5") to read specific pages, '
                "max 20 pages per request.",
            ),
            (
                "1-21",
                "Error: Requested 21 pages, at most 20 pages can be read "
                "per request.",
            ),
        ]:
            chunk = await self.read_tool(file_path=f.name, pages=pages)
            self.assertDictEqual(
                chunk.model_dump(mode="json"),
                {
                    "content": [
                        {
                            "type": "text",
                            "text": text,
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "state": "error",
                    "is_last": True,
                    "metadata": {},
                    "id": AnyString(),
                },
                pages,
            )

        chunk = await self.read_tool(file_path=f.name, pages="6-25")
        self.assertDictEqual(
            chunk.model_dump(mode="json"),
            {
                "content": [
                    {
                        "type": "text",
                        "text": "\n\n".join(
                            f"--- Page {i}/25 ---\n" for i in range(6, 26)
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_read_pdf_passthrough(self) -> None:
        """Test PDFs are handed to the model as DataBlock when accepted."""
        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        for _ in range(5):
            writer.add_blank_page(width=612, height=792)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            writer.write(f)
        self.addCleanup(os.unlink, f.name)
        with open(f.name, "rb") as fp:
            pdf_bytes = fp.read()

        tool = Read(model_input_types=["image/*", "application/pdf"])

        # Without pages the original bytes are returned untouched.
        chunk = await tool(file_path=f.name)
        self.assertDictEqual(
            chunk.model_dump(mode="json"),
            {
                "content": [
                    {
                        "type": "data",
                        "id": AnyString(),
                        "source": {
                            "type": "base64",
                            "data": base64.b64encode(pdf_bytes).decode(),
                            "media_type": "application/pdf",
                        },
                        "name": os.path.basename(f.name),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # With pages only the requested pages are kept.
        chunk = await tool(file_path=f.name, pages="2-3")
        self.assertEqual(chunk.content[0].source.media_type, "application/pdf")
        trimmed = base64.b64decode(chunk.content[0].source.data)
        self.assertEqual(len(PdfReader(io.BytesIO(trimmed)).pages), 2)

    async def test_read_unknown_extension_as_text(self) -> None:
        """Test files with unknown extensions are read as text."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            suffix=".xyz",
        ) as f:
            f.write("hello world\nsecond line\n")
        self.addCleanup(os.unlink, f.name)

        chunk = await self.read_tool(file_path=f.name)
        self.assertDictEqual(
            chunk.model_dump(mode="json"),
            {
                "content": [
                    {
                        "type": "text",
                        "text": "     1\thello world\n     2\tsecond line",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )
