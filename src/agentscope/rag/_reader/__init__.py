# -*- coding: utf-8 -*-
# -*- utf-8 -*-
"""The reader abstraction for retrieval-augmented generation (RAG)."""

from ._reader_base import ReaderBase, Document
from ._text_reader import TextReader


__all__ = [
    "Document",
    "ReaderBase",
    "TextReader",
]
