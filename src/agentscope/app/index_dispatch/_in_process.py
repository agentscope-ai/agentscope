# -*- coding: utf-8 -*-
"""In-process implementation of :class:`IndexDispatcherBase`.

Hands the document off to an :class:`~agentscope.app._service.
IndexWorker` instance that lives in the same process — what the
embedded deployment uses.  Dispatch wraps ``worker.process`` in an
``asyncio.create_task`` so the upload request can return ``201``
without waiting for the indexing pipeline to complete.

Background tasks are tracked so the app lifespan can ``await`` them on
shutdown; otherwise an in-flight indexing task would simply be torn
down when the event loop closes and any exception it raised would be
swallowed without surfacing.
"""
import asyncio
import logging
from typing import Any, Protocol, runtime_checkable

from ._base import IndexDispatcherBase


_logger = logging.getLogger(__name__)


@runtime_checkable
class _IndexWorkerProtocol(Protocol):
    """Subset of the worker interface needed by the in-process dispatcher.

    Declared as a protocol so the dispatcher can be unit-tested without
    instantiating the full worker (which depends on the blob store,
    knowledge base manager, parsers, chunker, ...).
    """

    async def process(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        ...


class InProcessDispatcher(IndexDispatcherBase):
    """Schedule indexing tasks as background asyncio tasks."""

    def __init__(self, worker: _IndexWorkerProtocol) -> None:
        """Initialize the dispatcher.

        Args:
            worker (`_IndexWorkerProtocol`):
                The worker that owns the parse → chunk → index pipeline.
        """
        self._worker = worker
        self._tasks: set[asyncio.Task[Any]] = set()

    async def dispatch(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Fire-and-forget schedule a worker.process invocation.

        The returned task is tracked in :attr:`_tasks` and removed on
        completion so its reference doesn't leak.  A failure inside
        ``worker.process`` is logged but not raised — the upload
        request has already returned ``201``, and the worker is
        responsible for persisting the error onto the document record
        before bubbling out.
        """
        task = asyncio.create_task(
            self._worker.process(
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
            ),
            name=f"index:{knowledge_base_id}:{document_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        """Drop the task reference and log any uncaught exception."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _logger.exception(
                "Background indexing task %s failed",
                task.get_name(),
                exc_info=exc,
            )

    async def aclose(self) -> None:
        """Cancel any in-flight indexing tasks and wait for them to settle.

        Called by the app lifespan on shutdown so the event loop does
        not close on top of running coroutines.  Worker code is
        expected to catch :class:`asyncio.CancelledError` and either
        complete the current phase cleanly or leave the document with
        a recoverable status (the lease will expire and the sweeper
        will pick it back up next start).
        """
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Drain background tasks on exit."""
        await self.aclose()
