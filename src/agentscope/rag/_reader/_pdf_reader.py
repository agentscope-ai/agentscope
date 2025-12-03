# -*- coding: utf-8 -*-
"""The PDF reader to read and chunk PDF files."""
import hashlib
from typing import Literal

from ._reader_base import ReaderBase
from ._text_reader import TextReader
from .._document import Document


class PDFReader(ReaderBase):
    """The PDF reader that splits text into chunks by a fixed chunk size."""

    def __init__(
        self,
        chunk_size: int = 512,
        split_by: Literal["char", "sentence", "paragraph"] = "sentence",
    ) -> None:
        """Initialize the text reader.

        Args:
            chunk_size (`int`, default to 512):
                The size of each chunk, in number of characters.
            split_by (`Literal["char", "sentence", "paragraph"]`, default to \
            "sentence"):
                The unit to split the text, can be "char", "sentence", or
                "paragraph". The "sentence" option is implemented using the
                "nltk" library, which only supports English text.
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

        # To avoid code duplication, we use TextReader to do the chunking.
        self._text_reader = TextReader(
            self.chunk_size,
            self.split_by,
        )

    async def __call__(
        self,
        pdf_path: str,
    ) -> list[Document]:
        """Read a PDF file, split it into chunks, and return a list of
        Document objects.

        Args:
            pdf_path (`str`):
                The input PDF file path.
        """
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise ImportError(
                "Please install pypdf to use the PDF reader. "
                "You can install it by `pip install pypdf`.",
            ) from e

        reader = PdfReader(pdf_path)

        gather_texts = []
        for page in reader.pages:
            gather_texts.append(page.extract_text())

        doc_id = hashlib.sha256(pdf_path.encode("utf-8")).hexdigest()

        docs = await self._text_reader("\n\n".join(gather_texts))
        for doc in docs:
            doc.id = doc_id

        return docs

    def get_doc_id(self, pdf_path: str) -> str:
        """Get the document ID. This function can be used to check if the
        doc_id already exists in the knowledge base."""
        return hashlib.sha256(pdf_path.encode("utf-8")).hexdigest()
