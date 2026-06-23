# -*- coding: utf-8 -*-
"""Abstract base class for index dispatchers.

A dispatcher tells "somewhere a worker exists" to start processing a
document.  Splitting "where the work runs" from "what the work does"
lets the same :class:`~agentscope.app._service.IndexWorker` code drive
two deployment modes:

- **embedded** — the worker lives inside the API process and dispatch
  is an ``asyncio.create_task`` call (:class:`InProcessDispatcher`).
- **dedicated** — the worker lives in a separate process subscribed to
  a message bus topic and dispatch becomes a ``publish`` call (the
  ``MessageBusDispatcher`` implementation slated for the dedicated
  deployment phase).

The interface stays narrow on purpose: a dispatcher takes the document
identity, hands it off, and returns.  It does not own retries, lease
acquisition, or status — those live in the worker and the storage
layer.  As a result every implementation MUST be idempotent under
double-dispatch: re-dispatching a document already being processed is
safe because the worker's lease CAS rejects the second attempt.
"""
from abc import ABC, abstractmethod
from typing import Any, Self


class IndexDispatcherBase(ABC):
    """Abstract base class for index dispatchers."""

    async def __aenter__(self) -> Self:
        """Enter the async context.  Default implementation is a no-op."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context.  Default implementation is a no-op."""

    @abstractmethod
    async def dispatch(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Schedule a document for indexing.

        Returns as soon as the hand-off is accepted; actual processing
        happens asynchronously inside a worker.  Implementations MUST
        be idempotent under double-dispatch — the worker's lease CAS
        is the de-duplication contract.

        Args:
            user_id (`str`):
                The owner user id.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The document id to process.
        """
