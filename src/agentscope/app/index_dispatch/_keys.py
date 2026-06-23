# -*- coding: utf-8 -*-
"""Bus key constants and payload schema for the index-task channel.

Centralised here so the :class:`~agentscope.app.message_bus.MessageBus`
itself does not have to know what an "index task" is. The dispatcher
and the worker-side consumer both reach for these constants when
calling the bus's transport-level primitives — there is no
``enqueue_index_task`` method on the bus, and there should not be one.

The split mirrors the wake-up channel (see
:class:`~agentscope.app.message_bus.MessageBusKeys` plus the helpers in
:mod:`agentscope.app._bus_ops`): transport-level primitives live on
the bus, domain-shaped operations live next to the domain.
"""
from typing import TypedDict


INDEX_TASKS_QUEUE = "agentscope:index:tasks"
"""Shared, durable index-task queue. The producer-side
:class:`~agentscope.app.index_dispatch.MessageBusDispatcher`
``queue_push`` here; the worker-process-side
:class:`~agentscope.app._service.IndexTaskConsumer` ``queue_drain`` it
on each signal — plus once eagerly on consumer start-up so tasks
queued while every worker was down are picked up immediately."""

INDEX_TASKS_SIGNAL = "agentscope:index:tasks:wake"
"""Shared pub/sub channel that nudges every running index-task
consumer to drain the queue. Payload is opaque — only its arrival
matters. Redis pub/sub is fire-and-forget, so a published signal does
not guarantee delivery; the durable queue keeps work safe in case
every subscriber happens to be offline."""


class IndexTaskPayload(TypedDict):
    """Shape of a single index-task entry on the queue.

    Kept as a :class:`TypedDict` rather than a Pydantic model because
    the queue store is the wire format — we do not want runtime
    validation overhead per drain, and the schema is small enough
    that producers can hand-construct it.

    The three fields are the minimum needed for
    :meth:`~agentscope.app._service.IndexWorker.process` to do its
    job. Anything else (status, blob URI, content type, retry count)
    is fetched fresh from storage by the worker — the queue entry is
    a *pointer*, not a snapshot.
    """

    user_id: str
    knowledge_base_id: str
    document_id: str
