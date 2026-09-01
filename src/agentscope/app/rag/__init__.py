# -*- coding: utf-8 -*-
"""Service-layer RAG building blocks.

This subpackage groups every RAG-specific service-layer concept under
one roof so the user-facing import surface stays compact:

- :mod:`.blob_store` — backends storing uploaded document bytes;
- :mod:`.knowledge_base_manager` — knowledge base lifecycle and the
  runtime :class:`~agentscope.rag.KnowledgeBase` handle shared between
  HTTP and agents;
- :mod:`.index_worker` — out-of-process indexing worker entry point.

Commonly used symbols are re-exported here so the typical user import
collapses to a single line::

    from agentscope.app.rag import (
        LocalBlobStore,
        S3BlobStore,
        CollectionPerKbManager,
        run_worker,
    )

For type-level imports or rare subclasses, use the submodules directly.
"""
from typing import TYPE_CHECKING

from .blob_store import (
    AsyncReadable,
    BlobStoreBase,
    LocalBlobStore,
    S3BlobStore,
)
from .knowledge_base_manager import (
    CollectionPerKbManager,
    DimensionPolicy,
    DimensionPolicyError,
    DimensionPolicyKind,
    KnowledgeBaseError,
    KnowledgeBaseManagerBase,
    KnowledgeBaseNotFoundError,
)

if TYPE_CHECKING:
    # Re-exported lazily at runtime via ``__getattr__`` to break a
    # package-load import cycle; declared here so static analysers
    # (pylint, mypy, IDEs) still see the symbol on the package.
    from .index_worker import run_worker  # noqa: F401


def __getattr__(name: str) -> object:
    """Lazy-load attributes that would cause an import cycle.

    ``run_worker`` is lazy-imported because :mod:`.index_worker` pulls
    in :mod:`agentscope.app._service`, which itself imports
    :mod:`agentscope.app.rag` for the middleware — a direct re-export
    would create an import cycle at package load.  Users who actually
    want the worker (``from agentscope.app.rag import run_worker``)
    pay the import cost on first access.

    Args:
        name (`str`):
            The attribute name requested by the importer.

    Returns:
        `object`:
            The resolved attribute value.

    Raises:
        `AttributeError`:
            When ``name`` is not exposed by this package.
    """
    if name == "run_worker":
        from .index_worker import run_worker as _run_worker

        return _run_worker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AsyncReadable",
    "BlobStoreBase",
    "CollectionPerKbManager",
    "DimensionPolicy",
    "DimensionPolicyError",
    "DimensionPolicyKind",
    "KnowledgeBaseError",
    "KnowledgeBaseManagerBase",
    "KnowledgeBaseNotFoundError",
    "LocalBlobStore",
    "S3BlobStore",
    "run_worker",
]
