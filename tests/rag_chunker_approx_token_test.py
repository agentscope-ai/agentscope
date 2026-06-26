# -*- coding: utf-8 -*-
"""Unit tests for the ApproxTokenChunker class."""
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.message import Base64Source, DataBlock, TextBlock
from agentscope.rag import ApproxTokenChunker, Chunk, Section


def _dump_chunks(chunks: list[Chunk]) -> list[dict]:
    """Convert chunks into plain dicts for whole-structure comparison.

    Args:
        chunks (`list[Chunk]`):
            The chunks to convert.

    Returns:
        `list[dict]`:
            The chunks as plain dicts.
    """
    return [chunk.model_dump() for chunk in chunks]


class ApproxTokenChunkerTest(IsolatedAsyncioTestCase):
    """The test cases for the ApproxTokenChunker class."""

    async def test_short_text_single_chunk(self) -> None:
        """Short text should produce a single chunk."""
        chunker = ApproxTokenChunker(chunk_size=512, overlap=50)
        sections = [
            Section(
                content=TextBlock(text="Hello world!"),
                source="a.txt",
                metadata={"page": 1},
            ),
        ]

        chunks = await chunker.chunk(sections)

        self.assertEqual(
            _dump_chunks(chunks),
            [
                {
                    "content": {
                        "type": "text",
                        "text": "Hello world!",
                        "id": AnyString(),
                    },
                    "source": "a.txt",
                    "chunk_index": 0,
                    "total_chunks": 1,
                    "metadata": {"page": 1},
                },
            ],
        )

    async def test_long_text_split_with_overlap(self) -> None:
        """Long text should be split into overlapping chunks."""
        # chunk_size=10 tokens -> 40-byte window, overlap=2 -> 8-byte step
        # back, so for ASCII text the windows start at 0, 32, 64, ...
        chunker = ApproxTokenChunker(chunk_size=10, overlap=2)
        text = "abcdefghij" * 20  # 200 ASCII chars => 50 approx tokens
        sections = [
            Section(
                content=TextBlock(text=text),
                source="b.txt",
            ),
        ]

        chunks = await chunker.chunk(sections)

        self.assertEqual(
            _dump_chunks(chunks),
            [
                {
                    "content": {
                        "type": "text",
                        "text": text[start : start + 40],
                        "id": AnyString(),
                    },
                    "source": "b.txt",
                    "chunk_index": index,
                    "total_chunks": 6,
                    "metadata": {},
                }
                for index, start in enumerate([0, 32, 64, 96, 128, 160])
            ],
        )

    async def test_data_block_pass_through(self) -> None:
        """DataBlock sections should pass through unchanged."""
        chunker = ApproxTokenChunker(chunk_size=10, overlap=2)
        data_block = DataBlock(
            source=Base64Source(data="aGk=", media_type="image/png"),
        )
        sections = [
            Section(
                content=TextBlock(text="x" * 100),
                source="c.pdf",
            ),
            Section(
                content=data_block,
                source="c.pdf",
                metadata={"page": 2},
            ),
        ]

        chunks = await chunker.chunk(sections)

        self.assertEqual(
            _dump_chunks(chunks),
            [
                {
                    "content": {
                        "type": "text",
                        "text": "x" * 40,
                        "id": AnyString(),
                    },
                    "source": "c.pdf",
                    "chunk_index": 0,
                    "total_chunks": 4,
                    "metadata": {},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "x" * 40,
                        "id": AnyString(),
                    },
                    "source": "c.pdf",
                    "chunk_index": 1,
                    "total_chunks": 4,
                    "metadata": {},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "x" * 36,
                        "id": AnyString(),
                    },
                    "source": "c.pdf",
                    "chunk_index": 2,
                    "total_chunks": 4,
                    "metadata": {},
                },
                {
                    "content": {
                        "type": "data",
                        "id": AnyString(),
                        "source": {
                            "type": "base64",
                            "data": "aGk=",
                            "media_type": "image/png",
                        },
                        "name": None,
                    },
                    "source": "c.pdf",
                    "chunk_index": 3,
                    "total_chunks": 4,
                    "metadata": {"page": 2},
                },
            ],
        )
        # The DataBlock instance itself is passed through, not copied
        self.assertIs(chunks[-1].content, data_block)

    async def test_no_cross_section_merging(self) -> None:
        """Chunks must never combine content from two sections."""
        chunker = ApproxTokenChunker(chunk_size=512, overlap=50)
        sections = [
            Section(content=TextBlock(text="first"), source="d.txt"),
            Section(content=TextBlock(text="second"), source="d.txt"),
        ]

        chunks = await chunker.chunk(sections)

        self.assertEqual(
            _dump_chunks(chunks),
            [
                {
                    "content": {
                        "type": "text",
                        "text": "first",
                        "id": AnyString(),
                    },
                    "source": "d.txt",
                    "chunk_index": 0,
                    "total_chunks": 2,
                    "metadata": {},
                },
                {
                    "content": {
                        "type": "text",
                        "text": "second",
                        "id": AnyString(),
                    },
                    "source": "d.txt",
                    "chunk_index": 1,
                    "total_chunks": 2,
                    "metadata": {},
                },
            ],
        )
