# -*- coding: utf-8 -*-
"""Abstract base class for chunkers.

A :class:`ChunkerBase` subclass takes the :class:`Section` list
produced by a :class:`~agentscope.rag.ParserBase` and splits the
content into final :class:`Chunk` objects suitable for embedding and
storage in a vector database.

Chunkers are **format-agnostic** — they operate on the unified
``TextBlock | DataBlock`` content carried in each Section.  Long
:class:`TextBlock` content is sliced according to a chunking strategy
(by character count, by tokens, by semantic boundaries, etc.); short
text and :class:`DataBlock` content are passed through unchanged.

Chunkers **never combine content across Section boundaries**.  This
guarantee preserves the structural metadata attached by the Parser
(page numbers, slide indices, embedded-image isolation, etc.).
"""
from abc import ABC, abstractmethod

from .._document import Chunk, Section


class ChunkerBase(ABC):
    """Abstract base class for chunkers.

    Subclasses implement a specific chunking strategy (by character
    count, by token count, by semantic boundary, etc.).  The
    chunker is configured once at construction time and reused
    across many ``chunk()`` calls within the same knowledge base.

    Subclasses must guarantee:

    - **No cross-Section merging**: every output :class:`Chunk` is
      derived from exactly one input :class:`Section`.
    - **DataBlock pass-through**: a Section whose content is a
      :class:`~agentscope.message.DataBlock` becomes a single Chunk
      with the same content; multimodal data is never sliced.
    - **Continuous indexing**: ``chunk_index`` runs from ``0`` to
      ``total_chunks - 1`` across the entire output list, even
      when the input contains many Sections.
    - **Consistent total_chunks**: every output Chunk carries the
      same ``total_chunks`` value (the length of the output list).
    - **Metadata inheritance**: each output Chunk's ``source`` and
      ``metadata`` are copied from its parent Section.
    """

    @abstractmethod
    async def chunk(self, sections: list[Section]) -> list[Chunk]:
        """Split a list of Sections into Chunks.

        Args:
            sections (`list[Section]`):
                The Sections produced by a :class:`ParserBase`, in
                document order.

        Returns:
            `list[Chunk]`:
                The final chunks, in document order, with
                ``chunk_index`` numbered ``0..N-1`` and
                ``total_chunks`` set to ``N`` on every chunk.
        """
