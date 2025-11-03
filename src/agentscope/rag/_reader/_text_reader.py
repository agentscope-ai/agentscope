# -*- coding: utf-8 -*-
"""The text reader that reads text into vector records."""
import hashlib
import os
from typing import Literal

from ._reader_base import ReaderBase, Document
from .._document import DocMetadata
from ..._logging import logger
from ...message import TextBlock


class TextReader(ReaderBase):
    """The text reader that splits text into chunks by a fixed chunk size
    and chunk overlap."""

    def __init__(
        self,
        chunk_size: int = 512,
        split_by: Literal["char", "sentence", "paragraph"] = "sentence",
    ) -> None:
        """Initialize the text reader.

        Args:
            chunk_size (`int`, default to 512):
                The size of each chunk, in number of characters.
            split_by (`Literal["char", "paragraph"]`, default to \
            "sentence"):
                The unit to split the text, can be "char", "sentence", or
                "paragraph". Note that "sentence" is implemented by "nltk"
                library, which only supports English text.
        """
        if chunk_size <= 0:
            raise ValueError(
                f"The chunk_size must be positive, got {chunk_size}",
            )

        if split_by not in ["char", "sentence", "paragraph"]:
            raise ValueError(
                "The split_by must be one of 'char', 'sentence' or "
                f"'paragraph', got {split_by}",
            )

        self.chunk_size = chunk_size
        self.split_by = split_by

    async def __call__(
        self,
        text: str,
    ) -> list[Document]:
        """Read a text string, split it into chunks, and return a list of
        Document objects.

        Args:
            text (`str`):
                The input text string, or a path to the local text file.

        Returns:
            `list[Document]`:
                A list of Document objects, where the metadata contains the
                chunked text, doc id and chunk id.
        """
        if os.path.exists(text) and os.path.isfile(text):
            logger.info("Reading text from local file: %s", text)
            with open(text, "r", encoding="utf-8") as file:
                text = file.read()

        logger.info(
            "Reading text with chunk_size=%d, split_by=%s",
            self.chunk_size,
            self.split_by,
        )
        splits = []
        if self.split_by == "char":
            # Split by character
            for i in range(0, len(text), self.chunk_size):
                start = max(0, i)
                end = min(i + self.chunk_size, len(text))
                splits.append(text[start:end])

        elif self.split_by == "sentence":
            try:
                import nltk

                nltk.download("punkt", quiet=True)
                nltk.download("punkt_tab", quiet=True)
            except ImportError as e:
                raise ImportError(
                    "nltk is not installed. Please install it with "
                    "`pip install nltk`.",
                ) from e

            sentences = nltk.sent_tokenize(text)

            # Handle the chunk_size for sentences
            processed_sentences = []
            for _ in sentences:
                if len(_) <= self.chunk_size:
                    processed_sentences.append(_)
                else:
                    # If the sentence itself exceeds chunk size, we need to
                    # truncate it
                    chunks = [
                        _[j : j + self.chunk_size]
                        for j in range(0, len(_), self.chunk_size)
                    ]
                    processed_sentences.extend(chunks)

            splits.extend(processed_sentences)

        elif self.split_by == "paragraph":
            paragraphs = [_ for _ in text.split("\n") if len(_)]
            for para in paragraphs:
                if len(para) <= self.chunk_size:
                    splits.append(para)

                else:
                    # If the paragraph itself exceeds chunk size, we need to
                    # truncate it
                    chunks = [
                        para[k : k + self.chunk_size]
                        for k in range(0, len(para), self.chunk_size)
                    ]
                    splits.extend(chunks)

        logger.info(
            "Finished splitting the text into %d chunks.",
            len(splits),
        )

        doc_id = self.get_doc_id(text)

        return [
            Document(
                id=doc_id,
                metadata=DocMetadata(
                    content=TextBlock(type="text", text=_),
                    doc_id=doc_id,
                    chunk_id=idx,
                    total_chunks=len(splits),
                ),
            )
            for idx, _ in enumerate(splits)
        ]

    def get_doc_id(self, text: str) -> str:
        """Get the document ID. This function can be used to check if the
        doc_id already exists in the knowledge base."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
