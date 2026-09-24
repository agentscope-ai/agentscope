# -*- coding: utf-8 -*-
"""Data structures used in the RAG indexing pipeline.

The indexing pipeline has two stages, each producing its own
structured output:

1. :class:`Section` — produced by a :class:`ParserBase` from a raw
   file.  Each ``Section`` represents one "natural boundary" of the
   source (a PDF page, a PPTX slide, an embedded image, a Markdown
   heading section, etc.).  A ``Chunker`` never combines content
   across two ``Section`` instances, so ``Section`` is also a hard
   boundary that prevents leakage of format-specific structure into
   downstream chunks.

2. :class:`Chunk` — produced by a :class:`ChunkerBase` from one or
   more ``Section`` instances.  Each ``Chunk`` is the final unit
   that gets embedded and inserted into the vector store.

Neither structure is persisted on its own — they are transient
in-memory carriers between pipeline stages.  Persistence happens at
the :class:`~agentscope.rag.VectorRecord` and
``KnowledgeDocumentRecord`` layers.
"""
from typing import Any

from pydantic import BaseModel, Field

from ..message import TextBlock, DataBlock


class Section(BaseModel):
    """A single natural section produced by a :class:`ParserBase`.

    A ``Section`` represents one logical region of the source file.
    The :class:`ChunkerBase` guarantees that no resulting
    :class:`Chunk` ever spans content from two different sections.

    The granularity of a ``Section`` is format-specific:

    - **PDF**: one section per page (plus separate sections for
      embedded images).
    - **PPTX**: one section per slide.
    - **Markdown**: one section per top-level heading, or the entire
      file if unstructured.
    - **TXT / image / video**: one section for the whole file.
    """

    content: TextBlock | DataBlock
    """The section content.  Text sections use :class:`TextBlock`;
    multimodal sections (images, video, etc.) use :class:`DataBlock`."""

    source: str
    """The source filename (e.g. ``"report.pdf"``).  Carried through
    to every downstream :class:`Chunk` and into the vector store
    metadata for citation / display."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Format-specific metadata written by the parser.  Examples:

    - PDFParser: ``{"page": 3}``
    - PPTXParser: ``{"slide": 2}``
    - ExcelParser: ``{"sheet": "Q3 Sales"}``

    These keys are not part of any retrieval / pipeline contract —
    they are passed through verbatim to the vector store metadata for
    later citation.  Each Chunk inherits this dict from its parent
    Section.
    """


class Chunk(BaseModel):
    """A final indexable chunk produced by a :class:`ChunkerBase`.

    Each ``Chunk`` corresponds to one record in the vector store.
    The required structural fields (``source``, ``chunk_index``,
    ``total_chunks``) enable downstream features such as "expand
    context around a hit" during retrieval.
    """

    content: TextBlock | DataBlock
    """The chunk content (sliced from a text :class:`Section`, or a
    multimodal :class:`DataBlock` passed through unchanged)."""

    source: str
    """The source filename — inherited from the parent
    :class:`Section`.  Used for display / citation."""

    chunk_index: int
    """The 0-based index of this chunk **within the document**.
    Sequential across all sections of the same source file.  Used to
    locate neighbouring chunks for "context expansion" at query time.
    """

    total_chunks: int
    """The total number of chunks produced from the same source file.
    Together with :attr:`chunk_index` lets callers know whether a hit
    is near the start / end of the document, and bounds the
    expansion range."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Format-specific metadata inherited from the parent
    :class:`Section`.  See :attr:`Section.metadata`."""
