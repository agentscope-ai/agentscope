# -*- coding: utf-8 -*-
"""Service that bridges team-member HITL events to leader sessions.

When a team *member* (worker) session hits a tool call that needs
human confirmation, the worker run parks on an ``ASKING`` tool call in
its **own** session — invisible to a client subscribed only to the
*leader* session's event stream. This service projects each such
pending request onto the leader session so the leader UI can render
and resolve it.

It is backed entirely by the message-bus generic registry primitives
(``registry_*``); no business methods are added to ``MessageBus``
itself. Key conventions are centralised in
:class:`~agentscope.app.message_bus.MessageBusKeys`.

Persistence model (see the design doc, §2.4):

- **Authoritative state** is the worker session's own ``state.context``
  (the ``ASKING`` tool call). This store is only a *projection*.
- The Redis hash written here is the **only durable** record of the
  leader-side pending card — the leader's event channel (replay log +
  pub/sub) is not durable across runs. The hash carries **no TTL**: a
  legitimate confirmation can stay pending indefinitely. Stale entries
  (worker cancelled/crashed without clearing) are healed by
  reconcile-on-read at SSE replay time.
"""
import json
from typing import TYPE_CHECKING

from ..message_bus import MessageBusKeys

if TYPE_CHECKING:
    from ..message_bus import MessageBus


class SubagentHitlInbox:
    """Per-leader-session store of pending subagent HITL requests.

    Persists in Redis via ``MessageBus.registry_*`` so it survives both
    page reloads and process restarts. Live notification piggy-backs on
    the leader session's existing event channel via
    ``MessageBus.session_publish_event`` — front-ends subscribe through
    the existing ``GET /sessions/{sid}/stream`` endpoint.

    The class is a thin stateless wrapper around the bus; construct one
    wherever needed (it holds only a bus reference).
    """

    EVT_REQUIRE = "subagent_require_user_confirm"
    """``CustomEvent.name`` used to push/replay a pending request to the
    leader's event stream."""

    EVT_RESULT = "subagent_user_confirm_result"
    """``CustomEvent.name`` used to tell the leader UI a pending request
    has been resolved and its card should be cleared."""

    def __init__(self, message_bus: "MessageBus") -> None:
        """Bind the message bus.

        Args:
            message_bus (`MessageBus`):
                Application message bus; only its generic ``registry_*``
                primitives are used.
        """
        self._bus = message_bus

    async def upsert(self, leader_sid: str, payload: dict) -> None:
        """Persist (or overwrite) one pending HITL request.

        Args:
            leader_sid (`str`):
                The leader session the request is projected onto.
            payload (`dict`):
                The entry to store. Must contain ``worker_session_id``
                and ``reply_id`` (used to derive the hash field key).
        """
        ns = MessageBusKeys.subagent_hitl_namespace(leader_sid)
        field = MessageBusKeys.subagent_hitl_field(
            payload["worker_session_id"],
            payload["reply_id"],
        )
        await self._bus.registry_set(ns, field, json.dumps(payload))

    async def resolve(self, leader_sid: str, reply_id: str) -> dict | None:
        """Find the pending entry for ``reply_id`` under this leader.

        Used by the confirm-routing entry point
        (:meth:`ChatService.run`): given a confirm result POSTed to the
        leader session, locate which worker session it actually belongs
        to so the result can be forwarded there.

        Args:
            leader_sid (`str`):
                The leader session the confirm result was POSTed to.
            reply_id (`str`):
                The worker-side reply id carried by the confirm result.

        Returns:
            `dict | None`:
                The stored payload (with ``worker_session_id`` /
                ``worker_agent_id``), or ``None`` when no pending entry
                matches — meaning the confirm is the leader's own.
        """
        raw = await self._bus.registry_getall(
            MessageBusKeys.subagent_hitl_namespace(leader_sid),
        )
        for value in raw.values():
            entry = json.loads(value)
            if entry.get("reply_id") == reply_id:
                return entry
        return None

    async def delete(
        self,
        leader_sid: str,
        worker_sid: str,
        reply_id: str,
    ) -> None:
        """Remove one pending entry.

        Idempotent: a no-op when the entry is already gone.

        Args:
            leader_sid (`str`):
                The leader session the entry was projected onto.
            worker_sid (`str`):
                The worker session that emitted the request.
            reply_id (`str`):
                The worker-side reply id.
        """
        await self._bus.registry_del(
            MessageBusKeys.subagent_hitl_namespace(leader_sid),
            MessageBusKeys.subagent_hitl_field(worker_sid, reply_id),
        )

    async def list(self, leader_sid: str) -> list[dict]:
        """Return every pending entry for a leader session.

        Args:
            leader_sid (`str`):
                The leader session.

        Returns:
            `list[dict]`:
                All stored payloads; empty when none are pending.
        """
        raw = await self._bus.registry_getall(
            MessageBusKeys.subagent_hitl_namespace(leader_sid),
        )
        return [json.loads(v) for v in raw.values()]

    async def purge(self, leader_sid: str) -> None:
        """Drop the entire pending store for a leader session.

        Used when the leader session (or its team) is deleted.

        Args:
            leader_sid (`str`):
                The leader session to purge.
        """
        await self._bus.registry_drop(
            MessageBusKeys.subagent_hitl_namespace(leader_sid),
        )

    async def drop_worker(self, leader_sid: str, worker_sid: str) -> None:
        """Drop every pending entry that originated from one worker.

        Used when a single worker session is deleted while the leader
        survives.

        Args:
            leader_sid (`str`):
                The leader session the entries are projected onto.
            worker_sid (`str`):
                The worker session whose entries should be dropped.
        """
        ns = MessageBusKeys.subagent_hitl_namespace(leader_sid)
        raw = await self._bus.registry_getall(ns)
        for field, value in raw.items():
            if json.loads(value).get("worker_session_id") == worker_sid:
                await self._bus.registry_del(ns, field)
