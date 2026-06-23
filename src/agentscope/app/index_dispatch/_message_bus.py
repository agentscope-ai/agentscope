# -*- coding: utf-8 -*-
"""Message-bus implementation of :class:`IndexDispatcherBase`.

Hands the document off via the shared message bus instead of
``asyncio.create_task``: dispatch pushes a structured payload onto
the durable :data:`INDEX_TASKS_QUEUE` and publishes a signal on
:data:`INDEX_TASKS_SIGNAL`, exactly mirroring the wake-up flow.

What makes this safe for production:

- The push is durable (Mode A queue, backed by a Redis list / stream
  in the production backend), so a task survives every running
  worker being offline.
- The publish is best-effort but covers the hot path: when at least
  one worker is connected to the channel, it picks up the task
  within one ``subscribe`` round-trip.
- A worker that starts AFTER the publish was lost still sees the
  task — its :class:`IndexTaskConsumer` performs an eager drain
  on context entry.
- The :class:`IndexSweeper` keeps running in the API process even
  in dedicated mode, so a task whose dispatcher write succeeded but
  whose worker never picked it up is re-dispatched after the
  pending grace period elapses.

The dispatcher itself is stateless: there is nothing to start, stop,
or clean up. The async-context-manager hooks inherit the no-op
defaults from :class:`IndexDispatcherBase`.
"""
from typing import TYPE_CHECKING

from ._base import IndexDispatcherBase
from ._keys import (
    INDEX_TASKS_QUEUE,
    INDEX_TASKS_SIGNAL,
    IndexTaskPayload,
)

if TYPE_CHECKING:
    from ..message_bus import MessageBus


class MessageBusDispatcher(IndexDispatcherBase):
    """Dispatch index tasks across processes through the message bus.

    Used when the API process does not host an
    :class:`~agentscope.app._service.IndexWorker` itself
    (``enable_index_worker=False`` in the app lifespan). Pair with
    one or more :class:`~agentscope.app._service.IndexTaskConsumer`
    instances running in dedicated worker processes.

    Args:
        message_bus (`MessageBus`):
            Application message bus. The dispatcher only uses the two
            transport-level primitives — ``queue_push`` and
            ``publish`` — so any bus backend works.
    """

    def __init__(self, message_bus: "MessageBus") -> None:
        self._bus = message_bus

    async def dispatch(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Push the task onto the shared queue and fire the wake signal.

        The two-step order matters: the queue push happens *first*,
        so a worker woken by the signal is guaranteed to see the
        entry when it drains.

        Idempotency: re-dispatching the same document is safe because
        the worker's lease CAS rejects duplicates. The queue may
        legitimately hold multiple entries for the same document
        (one from upload, one from sweeper) and the second one will
        be a no-op at the worker.
        """
        payload: IndexTaskPayload = {
            "user_id": user_id,
            "knowledge_base_id": knowledge_base_id,
            "document_id": document_id,
        }
        await self._bus.queue_push(INDEX_TASKS_QUEUE, dict(payload))
        await self._bus.publish(INDEX_TASKS_SIGNAL, {})
