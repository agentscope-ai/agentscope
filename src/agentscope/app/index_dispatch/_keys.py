# -*- coding: utf-8 -*-
"""Payload schema for the index-task channel.

The channel's bus keys themselves live on
:class:`~agentscope.app.message_bus.MessageBusKeys` so they sit in the
same registry as every other application-layer key (wakeup queue,
session inbox, session events, …) and can be audited, migrated, or
re-namespaced from one place. The push + signal composition that
producers run lives on :func:`~agentscope.app._bus_ops.enqueue_index_task`,
alongside the analogous wake-up helper. What stays here is the
*wire shape* of a single queue entry — the schema is domain data, not
a transport key, and giving it its own module keeps the dispatcher
and the consumer pointing at one definition.

Background: :class:`~agentscope.app.message_bus.MessageBus` itself
stays domain-agnostic. The dispatcher delegates to
:func:`enqueue_index_task` and the worker-side consumer reaches for
``MessageBusKeys.index_tasks_queue()`` /
``MessageBusKeys.index_tasks_signal()`` when calling the bus's
transport primitives — there is no ``enqueue_index_task`` method on
the bus, and there should not be one.
"""
from typing import TypedDict


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
