# -*- coding: utf-8 -*-
"""Generic cross-session UI projection primitive.

A *projection* mirrors a UI card owned by one session onto another
session's event stream, so a client subscribed only to the target
session can render and resolve it. The canonical use is team HITL: a
worker (member) session parks on a tool call awaiting confirmation in
its own session — invisible to a client watching only the *leader* —
so the pending request is projected onto the leader.

This class is **pure mechanism**: it knows nothing about teams,
workers, leaders, or HITL. It is the reusable substrate every such
feature shares — a durable per-session hash plus a live notification —
so a new projection feature is a small strategy object (an
``EventProjector``) over this primitive, not a new bus wrapper class.

Backed entirely by the message-bus generic registry primitives
(``registry_*``) and :meth:`MessageBus.session_publish_event`; no
business methods are added to ``MessageBus``. Key conventions live in
:class:`~agentscope.app.message_bus.MessageBusKeys`.

Persistence model:

- The Redis hash is the **only durable** record of a projected card —
  the target session's event channel (replay log + pub/sub) is not
  durable across runs. It carries **no TTL**: a legitimate card can
  stay pending indefinitely. Authoritative truth for whether a card is
  still live lives in the *owning* session's own state; stale hash
  entries are healed by reconcile-on-read at SSE replay time, not by
  expiry.
- ``kind`` partitions the hash so one target session can host several
  independent feeds (HITL, progress, errors, …) without collision.
"""
import json
from typing import TYPE_CHECKING

from ..message_bus import MessageBusKeys
from ...event import CustomEvent

if TYPE_CHECKING:
    from ..message_bus import MessageBus


class SessionProjection:
    """Durable per-session store of UI cards projected from elsewhere.

    A thin stateless wrapper around the message bus — construct one
    wherever needed (it holds only a bus reference). Entries are grouped
    per ``(target_session_id, kind)``; within a feed each entry is keyed
    by a caller-chosen ``entry_id``.

    Live notification piggybacks on the target session's existing event
    channel via :meth:`publish`, so front-ends receive updates over the
    same ``GET /sessions/{sid}/stream`` SSE connection they already use.
    """

    def __init__(self, message_bus: "MessageBus") -> None:
        """Bind the message bus.

        Args:
            message_bus (`MessageBus`):
                Application message bus; only its generic ``registry_*``
                primitives and :meth:`session_publish_event` are used.
        """
        self._bus = message_bus

    async def upsert(
        self,
        target_sid: str,
        kind: str,
        entry_id: str,
        payload: dict,
    ) -> None:
        """Persist (or overwrite) one projected entry.

        Args:
            target_sid (`str`):
                The session the entry is projected onto.
            kind (`str`):
                The projection feed (e.g. ``"subagent_hitl"``).
            entry_id (`str`):
                Identity of the entry within the feed.
            payload (`dict`):
                The entry to store (JSON-serializable).
        """
        await self._bus.registry_set(
            MessageBusKeys.projection_namespace(target_sid),
            MessageBusKeys.projection_field(kind, entry_id),
            json.dumps(payload),
        )

    async def delete(
        self,
        target_sid: str,
        kind: str,
        entry_id: str,
    ) -> None:
        """Remove one projected entry.

        Idempotent: a no-op when the entry is already gone.

        Args:
            target_sid (`str`):
                The session the entry was projected onto.
            kind (`str`):
                The projection feed.
            entry_id (`str`):
                Identity of the entry within the feed.
        """
        await self._bus.registry_del(
            MessageBusKeys.projection_namespace(target_sid),
            MessageBusKeys.projection_field(kind, entry_id),
        )

    async def list(self, target_sid: str, kind: str) -> list[dict]:
        """Return every entry in one feed for a target session.

        Args:
            target_sid (`str`):
                The session whose projections to read.
            kind (`str`):
                The projection feed to filter by.

        Returns:
            `list[dict]`:
                All stored payloads in the feed; empty when none.
        """
        raw = await self._bus.registry_getall(
            MessageBusKeys.projection_namespace(target_sid),
        )
        prefix = MessageBusKeys.projection_field_prefix(kind)
        return [
            json.loads(value)
            for field, value in raw.items()
            if field.startswith(prefix)
        ]

    async def purge(self, target_sid: str, kind: str | None = None) -> None:
        """Drop projected entries for a target session.

        Args:
            target_sid (`str`):
                The session to purge.
            kind (`str | None`, optional):
                When given, drop only that feed's entries (preserving
                other feeds on the same session). When ``None``, drop
                the session's entire projection store in one shot.
        """
        if kind is None:
            await self._bus.registry_drop(
                MessageBusKeys.projection_namespace(target_sid),
            )
            return
        ns = MessageBusKeys.projection_namespace(target_sid)
        prefix = MessageBusKeys.projection_field_prefix(kind)
        raw = await self._bus.registry_getall(ns)
        for field in raw:
            if field.startswith(prefix):
                await self._bus.registry_del(ns, field)

    async def publish(
        self,
        target_sid: str,
        event_name: str,
        value: dict,
    ) -> None:
        """Send a live ``CustomEvent`` to a target session's channel.

        Notifies front-ends subscribed to the target session that a
        projected card should be rendered or cleared.

        Args:
            target_sid (`str`):
                The session to notify.
            event_name (`str`):
                The ``CustomEvent.name`` carried to the front-end.
            value (`dict`):
                The event payload.
        """
        custom = CustomEvent(name=event_name, value=value)
        await self._bus.session_publish_event(
            target_sid,
            custom.model_dump(mode="json"),
        )
