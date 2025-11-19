# -*- coding: utf-8 -*-
"""The reader abstraction for retrieval-augmented generation (RAG)."""

from ._reader_base import ReaderBase, Document
from ._text_reader import TextReader
from ._pdf_reader import PDFReader
from ._word_reader import WordReader
from ._image_reader import ImageReader


__all__ = [
    "Document",
    "ReaderBase",
    "TextReader",
    "PDFReader",
    "WordReader",
    "ImageReader",
]
