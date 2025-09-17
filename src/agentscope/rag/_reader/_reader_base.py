# -*- coding: utf-8 -*-
"""The reader base class for retrieval-augmented generation (RAG)."""
from abc import abstractmethod
from typing import Any

from .._document import Document


class ReaderBase:
    """The reader base class, which is responsible for reading the original
    data, splitting it into chunks, and converting each chunk into a `Document`
    object."""

    @abstractmethod
    def __call__(self, *args: Any, **kwargs: Any) -> list[Document]:
        """The function that takes the input files and returns the
        vector records"""
