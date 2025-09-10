# -*- coding: utf-8 -*-
"""The text reader that reads text into vector records."""
import hashlib

import numpy as np

from ._reader_base import ReaderBase, Document
from .. import VectorRecord
from ...embedding import EmbeddingModelBase
from ...message import TextBlock


class TextReader(ReaderBase):
    """The text reader that reads text into vector records."""
    def __init__(self, embedding_model: EmbeddingModelBase) -> None:
        """Initialize the text reader.

        Args:
            embedding_model (`EmbeddingModelBase`):
                The embedding model to use for generating embeddings.
        """
        self.embedding_model = embedding_model


    async def __call__(
        self,
        text: str,
        chunk_size: int = 512,
    ) -> list[Document]:
        """Read from a text string and return a list of vector records.

        Args:
            text (`str`):
                The input text string.
            chunk_size (`int`, default to 512):
                The size of each chunk, in number of characters.
        """

        texts = [
            text[i:i + chunk_size] for i in range(0, len(text), chunk_size)
        ]

        embedding_res = await self.embedding_model(texts)

        # hash整个文本作为id
        doc_id = hashlib.sha256(text.encode()).hexdigest()

        return [
            Document(
                content=TextBlock(type="text", text=texts[idx]),
                doc_id=doc_id,
                chunk_id=idx,
                total_chunks=len(texts),
            ) for idx, _ in enumerate(embedding_res.embeddings)
        ]
