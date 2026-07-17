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
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from .._document import Chunk, Section


class ChunkerBase(ABC):
    """Abstract base class for chunkers.

    Subclasses implement a specific chunking strategy (by character
    count, by token count, by semantic boundary, etc.).  The
    chunker is configured once at construction time and reused
    across many ``chunk()`` calls within the same knowledge base.

    Subclasses must declare a :attr:`chunker_type` class variable
    that uniquely identifies the strategy.  The value is persisted
    in :class:`~agentscope.app.storage.KnowledgeBaseRecord` so the
    index worker can reconstruct the chunker at processing time.

    Subclasses should define a :class:`Parameters` inner class (a
    Pydantic :class:`BaseModel`) to declare their tunable knobs.
    The base class provides sensible defaults for
    :meth:`parameter_schema` and :meth:`from_config` when the inner
    class exists, mirroring how :class:`ModelCard` and
    :class:`RAGMiddleware` expose their parameters.

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

    chunker_type: ClassVar[str]
    """Unique identifier for this chunking strategy.  Must be set
    by every concrete subclass."""

    class Parameters(BaseModel):
        """Tunable parameters for this chunker.

        Subclasses override this inner class to declare their own
        parameters with Pydantic ``Field`` annotations.  The base
        version is empty (no tunable parameters).
        """

        model_config = ConfigDict(extra="forbid")

    @classmethod
    def parameter_schema(cls) -> dict[str, Any]:
        """Return a JSON Schema describing this chunker's tunable parameters.

        The schema is generated from the :class:`Parameters` inner
        class via ``model_json_schema()``, following the same pattern
        used by :class:`~agentscope.model.ModelCard` and
        :class:`~agentscope.middleware.RAGMiddleware`.

        Returns:
            `dict[str, Any]`:
                A JSON Schema object derived from :class:`Parameters`.
        """
        return cls.Parameters.model_json_schema()

    @classmethod
    def from_config(cls, **kwargs: Any) -> "ChunkerBase":
        """Instantiate this chunker from persisted configuration.

        Validates ``kwargs`` against the :class:`Parameters` model
        before forwarding to the constructor.  This ensures that
        stored parameters are type-checked and constraint-validated
        at reconstruction time.

        Args:
            **kwargs:
                Parameter values previously stored in the knowledge
                base record's ``chunker_config.parameters``.

        Returns:
            `ChunkerBase`:
                A configured chunker instance.

        Raises:
            `ValidationError`:
                If ``kwargs`` fail Pydantic validation.
        """
        params = cls.Parameters(**kwargs)
        return cls(**params.model_dump())

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
