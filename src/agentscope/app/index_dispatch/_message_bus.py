# -*- coding: utf-8 -*-
"""Message-bus implementation of :class:`IndexDispatcherBase`.

Thin adapter over :func:`~agentscope.app._bus_ops.enqueue_index_task`:
the real work — pushing a structured payload onto the durable
:meth:`~agentscope.app.message_bus.MessageBusKeys.index_tasks_queue`
and publishing the
:meth:`~agentscope.app.message_bus.MessageBusKeys.index_tasks_signal`
wake-up — lives in the shared bus-ops module so the same composition
is reusable outside the dispatcher abstraction (the sweeper, for
example, could call it directly if we ever drop the abstraction).

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
or clean up.  The async-context-manager hooks inherit the no-op
defaults from :class:`IndexDispatcherBase`.  It exists only because
the embedded vs dedicated split is modelled as two
:class:`IndexDispatcherBase` implementations injected into
:class:`~agentscope.app._service.KnowledgeBaseService`; without that
abstraction this class would not exist at all.
"""
from typing import TYPE_CHECKING

from ._base import IndexDispatcherBase
from .._bus_ops import enqueue_index_task

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
            ``publish`` — through :func:`enqueue_index_task`, so any
            bus backend works.
    """

    def __init__(self, message_bus: "MessageBus") -> None:
        self._bus = message_bus

    async def dispatch(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Hand the task off via the shared bus-ops helper."""
        await enqueue_index_task(
            self._bus,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
