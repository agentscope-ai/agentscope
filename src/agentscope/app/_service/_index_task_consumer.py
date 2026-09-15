# -*- coding: utf-8 -*-
"""Single per-worker-process consumer of the shared index-task channel.

One asyncio task per worker process. Subscribes to the shared
:meth:`~agentscope.app.message_bus.MessageBusKeys.index_tasks_signal`
channel and drains the durable
:meth:`~agentscope.app.message_bus.MessageBusKeys.index_tasks_queue`
on each signal. For each queued entry it invokes
:meth:`IndexWorker.process` directly — the worker holds its own
semaphore so we can fire-and-forget multiple ``process`` calls without
overrunning resources.

Mirrors :class:`~agentscope.app._manager.WakeupDispatcher`. The two
patterns are deliberately identical: both subscribe to a signal,
drain a queue, dispatch each entry, and run forever inside an
``async with`` block. Keeping them shaped the same makes it cheap to
reason about either one once you've read the other.

The bus exposes only transport-level primitives — there is no
``enqueue_index_task`` or ``dequeue_index_task`` method on it. The
key constants live on :class:`~agentscope.app.message_bus.
MessageBusKeys` (next to every other application-layer key) and the
composition is inline here because the consumer is the only sink for
the channel; introducing a separate ``IndexTaskBroker`` would be
ceremony without gain.
"""
import asyncio
from typing import TYPE_CHECKING, Any, Self

from ..message_bus import MessageBusKeys
from ..._logging import logger

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ._index_worker import IndexWorker


class IndexTaskConsumer:
    """Subscribe-then-drain consumer that feeds :class:`IndexWorker`.

    Args:
        message_bus (`MessageBus`):
            Application message bus. The consumer only uses the two
            transport-level primitives — ``subscribe`` (for the
            signal channel) and ``queue_drain`` (for the durable
            task queue).
        worker (`IndexWorker`):
            The worker that owns the parse → chunk → index pipeline.
            ``process`` is invoked once per queue entry; the worker's
            internal semaphore + lease CAS handle concurrency and
            deduplication.
        max_batch (`int`, defaults to ``32``):
            Maximum entries drained per signal. Keeps a single
            signal from monopolising the loop when the queue is
            backed up; remaining entries are picked up on the next
            signal or the next sweeper-driven eager drain.
    """

    def __init__(
        self,
        message_bus: "MessageBus",
        worker: "IndexWorker",
        max_batch: int = 32,
    ) -> None:
        self._bus = message_bus
        self._worker = worker
        self._max_batch = max_batch
        self._task: asyncio.Task | None = None
        # In-flight ``worker.process`` calls. Tracked so ``__aexit__``
        # can cancel + drain them; otherwise the event-loop teardown
        # would swallow exceptions raised inside the worker.
        self._inflight: set[asyncio.Task[Any]] = set()

    async def __aenter__(self) -> Self:
        """Start the consumer loop and wait until its subscription
        is live.

        After the subscription is established, an initial drain runs
        synchronously so tasks queued while every worker was down
        get picked up immediately on startup, without waiting for
        a fresh signal.
        """
        ready = asyncio.Event()
        self._task = asyncio.create_task(
            self._loop(ready),
            name="index-task-consumer",
        )
        await ready.wait()
        await self._drain_and_dispatch()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Cancel the consumer loop and drain any in-flight work.

        Cancellation of the worker's ``process`` calls is a clean
        shutdown signal — the worker holds the storage lease and
        will let it expire so the sweeper re-dispatches the document
        on the next loop tick.
        """
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        for task in list(self._inflight):
            task.cancel()
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
        self._inflight.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _loop(self, ready: asyncio.Event) -> None:
        """Long-lived loop: subscribe to the signal and drain on each
        received signal.

        Args:
            ready (`asyncio.Event`):
                Signalled after the underlying SUBSCRIBE completes.
                :meth:`__aenter__` blocks on this so the producer
                can publish a signal immediately after start-up
                without racing.
        """
        try:
            async for _signal in self._bus.subscribe(
                MessageBusKeys.index_tasks_signal(),
                on_ready=ready.set,
            ):
                await self._drain_and_dispatch()
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "IndexTaskConsumer loop crashed; subscription ended.",
            )
        finally:
            # If ``subscribe`` raises before ``on_ready`` fires, the
            # ``__aenter__`` coroutine would deadlock on ``ready.wait()``.
            # Set the event unconditionally on the way out so startup
            # cannot stall on a transient bus failure.
            ready.set()

    async def _drain_and_dispatch(self) -> None:
        """Read up to a batch of task entries and dispatch each one."""
        try:
            entries = await self._bus.queue_drain(
                MessageBusKeys.index_tasks_queue(),
                max_count=self._max_batch,
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception("IndexTaskConsumer: drain failed.")
            return

        for _entry_id, payload in entries:
            try:
                user_id = payload["user_id"]
                knowledge_base_id = payload["knowledge_base_id"]
                document_id = payload["document_id"]
            except (KeyError, TypeError):
                logger.warning(
                    "IndexTaskConsumer: skipping malformed entry %r",
                    payload,
                )
                continue

            self._spawn(
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
            )

    def _spawn(
        self,
        *,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Run :meth:`IndexWorker.process` as a tracked background task.

        We do not ``await`` ``worker.process`` inline — a slow parse
        would block draining the next signal. The worker holds its
        own concurrency semaphore, so spawning many tasks at once is
        safe; they will queue at the semaphore.
        """
        task = asyncio.create_task(
            self._worker.process(
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
            ),
            name=f"index-task:{knowledge_base_id}:{document_id}",
        )
        self._inflight.add(task)
        task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        """Drop the task reference and log any uncaught exception."""
        self._inflight.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception(
                "IndexTaskConsumer: worker.process(%s) raised",
                task.get_name(),
                exc_info=exc,
            )
