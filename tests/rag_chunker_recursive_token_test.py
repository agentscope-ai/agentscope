# -*- coding: utf-8 -*-
"""Unit tests for the RecursiveTokenChunker class."""
from unittest.async_case import IsolatedAsyncioTestCase

from pydantic import ValidationError

from utils import AnyString

from agentscope.message import Base64Source, DataBlock, TextBlock
from agentscope.rag import Chunk, RecursiveTokenChunker, Section


def _dump_chunks(chunks: list[Chunk]) -> list[dict]:
    """Convert chunks into plain dictionaries for comparison.

    Args:
        chunks (`list[Chunk]`):
            The chunks to convert.

    Returns:
        `list[dict]`:
            The serialized chunk structures.
    """
    return [chunk.model_dump() for chunk in chunks]


def _text_chunk(
    text: str,
    source: str,
    index: int,
    total: int,
    metadata: dict | None = None,
) -> dict:
    """Build the complete expected structure of a text chunk.

    Args:
        text (`str`):
            Expected text content.
        source (`str`):
            Expected source name.
        index (`int`):
            Expected chunk index.
        total (`int`):
            Expected total chunk count.
        metadata (`dict | None`, optional):
            Expected metadata.

    Returns:
        `dict`:
            The expected serialized chunk.
    """
    return {
        "content": {
            "type": "text",
            "text": text,
            "id": AnyString(),
            "created_at": AnyString(),
            "finished_at": None,
        },
        "source": source,
        "chunk_index": index,
        "total_chunks": total,
        "metadata": metadata or {},
    }


def _make_chunker(
    chunk_size: int,
    overlap: int,
    separators: list[str] | None = None,
) -> RecursiveTokenChunker:
    """Build a recursive chunker with explicit typed parameters."""
    parameters = {
        "chunk_size": chunk_size,
        "overlap": overlap,
    }
    if separators is not None:
        parameters["separators"] = separators
    return RecursiveTokenChunker(
        RecursiveTokenChunker.Parameters(**parameters),
    )


class RecursiveTokenChunkerTest(IsolatedAsyncioTestCase):
    """Test recursive, token-aware chunking behavior."""

    async def test_short_text_single_chunk(self) -> None:
        """Short text should produce one complete chunk."""
        chunker = _make_chunker(chunk_size=512, overlap=50)
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
                _text_chunk(
                    "Hello world!",
                    "a.txt",
                    0,
                    1,
                    {"page": 1},
                ),
            ],
        )

    async def test_recursive_separator_priority(self) -> None:
        """Paragraph boundaries should be preferred and preserved."""
        chunker = _make_chunker(chunk_size=5, overlap=0)
        text = "alpha beta.\n\ngamma delta.\n\nomega"
        sections = [Section(content=TextBlock(text=text), source="b.txt")]

        chunks = await chunker.chunk(sections)

        self.assertEqual(
            _dump_chunks(chunks),
            [
                _text_chunk("alpha beta.\n\n", "b.txt", 0, 2),
                _text_chunk("gamma delta.\n\nomega", "b.txt", 1, 2),
            ],
        )

    async def test_short_sentences_are_merged(self) -> None:
        """Short sentence splits should be packed up to the budget."""
        chunker = _make_chunker(chunk_size=4, overlap=0)
        sections = [
            Section(
                content=TextBlock(text="One. Two. Three. Four."),
                source="sentences.txt",
            ),
        ]

        chunks = await chunker.chunk(sections)

        self.assertEqual(
            _dump_chunks(chunks),
            [
                _text_chunk("One. Two. ", "sentences.txt", 0, 2),
                _text_chunk("Three. Four.", "sentences.txt", 1, 2),
            ],
        )

    async def test_chinese_sentence_boundaries(self) -> None:
        """Chinese punctuation should form natural split boundaries."""
        chunker = _make_chunker(chunk_size=5, overlap=0)
        sections = [
            Section(
                content=TextBlock(text="甲乙。丙丁。戊己。庚辛。"),
                source="zh.txt",
            ),
        ]

        chunks = await chunker.chunk(sections)

        self.assertEqual(
            _dump_chunks(chunks),
            [
                _text_chunk("甲乙。丙丁。", "zh.txt", 0, 2),
                _text_chunk("戊己。庚辛。", "zh.txt", 1, 2),
            ],
        )

    async def test_character_fallback_with_overlap(self) -> None:
        """Unbroken text should use UTF-8-safe overlapping windows."""
        chunker = _make_chunker(chunk_size=2, overlap=1)
        sections = [
            Section(
                content=TextBlock(text="abcdefghijklmnopqrst"),
                source="c.txt",
            ),
        ]

        chunks = await chunker.chunk(sections)

        expected_texts = [
            "abcdefgh",
            "efghijkl",
            "ijklmnop",
            "mnopqrst",
        ]
        self.assertEqual(
            _dump_chunks(chunks),
            [
                _text_chunk(text, "c.txt", index, 4)
                for index, text in enumerate(expected_texts)
            ],
        )

    async def test_data_block_and_section_isolation(self) -> None:
        """DataBlock content and separate Sections should stay isolated."""
        chunker = _make_chunker(chunk_size=2, overlap=0)
        data_block = DataBlock(
            source=Base64Source(data="aGk=", media_type="image/png"),
        )
        sections = [
            Section(content=TextBlock(text="first second"), source="d.pdf"),
            Section(
                content=data_block,
                source="d.pdf",
                metadata={"page": 2},
            ),
            Section(content=TextBlock(text="tail"), source="d.pdf"),
        ]

        chunks = await chunker.chunk(sections)

        self.assertEqual(
            _dump_chunks(chunks),
            [
                _text_chunk("first ", "d.pdf", 0, 4),
                _text_chunk("second", "d.pdf", 1, 4),
                {
                    "content": {
                        "type": "data",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                        "source": {
                            "type": "base64",
                            "data": "aGk=",
                            "media_type": "image/png",
                        },
                        "name": None,
                    },
                    "source": "d.pdf",
                    "chunk_index": 2,
                    "total_chunks": 4,
                    "metadata": {"page": 2},
                },
                _text_chunk("tail", "d.pdf", 3, 4),
            ],
        )
        self.assertIs(chunks[2].content, data_block)

    def test_parameters_and_schema(self) -> None:
        """Parameters should validate and expose a frontend schema."""
        parameters = RecursiveTokenChunker.Parameters(
            chunk_size=8,
            overlap=2,
            separators=["|", ""],
        )
        chunker = RecursiveTokenChunker(parameters)

        self.assertEqual(
            {
                "chunk_size": chunker.chunk_size,
                "overlap": chunker.overlap,
                "separators": chunker.separators,
            },
            {
                "chunk_size": 8,
                "overlap": 2,
                "separators": ["|", ""],
            },
        )
        schema = RecursiveTokenChunker.Parameters.model_json_schema()
        self.assertEqual(
            set(schema["properties"]),
            {"chunk_size", "overlap", "separators"},
        )
        self.assertEqual(
            schema["properties"]["separators"]["default"],
            [
                "\n\n",
                "\n",
                "。",
                "！",
                "？",
                ". ",
                "! ",
                "? ",
                "；",
                "; ",
                "，",
                ", ",
                " ",
                "",
            ],
        )

        with self.assertRaises(ValidationError):
            RecursiveTokenChunker.Parameters(
                chunk_size=8,
                overlap=8,
            )
        with self.assertRaises(ValidationError):
            RecursiveTokenChunker.Parameters(
                chunk_size=8,
                overlap=0,
                separators=[],
            )
