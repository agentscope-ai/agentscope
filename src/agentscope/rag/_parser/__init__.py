# -*- coding: utf-8 -*-
"""File parser implementations for the RAG indexing pipeline."""

from ._base import ParserBase
from ._text import TextParser

__all__ = [
    "ParserBase",
    "TextParser",
]
