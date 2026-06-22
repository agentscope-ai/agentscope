# -*- coding: utf-8 -*-
"""Projector that bridges team-member HITL events to leader sessions.

When a team *member* (worker) session hits a tool call that needs human
confirmation, the worker run parks on an ``ASKING`` tool call in its
**own** session — invisible to a client subscribed only to the *leader*
session's event stream. This projector mirrors each such pending
request onto the leader session so the leader UI can render and resolve
it, and clears it when the request is answered.

It is a thin strategy over the generic
:class:`~agentscope.app._service._session_projection.SessionProjection`
primitive: this file holds only the HITL-specific policy (which events
matter, how to resolve the leader, what the card payload looks like).
The durable hash, live notification, and key conventions all live in
the shared primitive.

Persistence model (see the design doc, §2.4):

- **Authoritative state** is the worker session's own ``state.context``
  (the ``ASKING`` tool call). The projection is only a mirror.
- The projected hash entry carries **no TTL**: a legitimate
  confirmation can stay pending indefinitely. Stale entries (worker
  cancelled/crashed without clearing) are healed by reconcile-on-read
  at SSE replay time.
"""
from datetime import datetime
from typing import TYPE_CHECKING

from ....event import (
    RequireUserConfirmEvent,
    RequireExternalExecutionEvent,
    UserConfirmResultEvent,
    ExternalExecutionResultEvent,
    ReplyEndEvent,
)

if TYPE_CHECKING:
    from ...storage import AgentRecord, SessionRecord, StorageBase
    from ....event import AgentEvent
    from .._session_projection import SessionProjection


class SubagentHitlProjector:
    """Project pending team-member HITL requests onto leader sessions.

    Holds the storage handle it needs to resolve a worker's team (and
    thus its leader). The :class:`SessionProjection` it writes through
    is passed in per call by
    :class:`~agentscope.app._service.ChatService`, which also resolves
    confirm-routing and SSE replay through this projector's
    :meth:`resolve` / :meth:`entry_id` helpers.
    """

    KIND = "subagent_hitl"
    """Projection feed key (namespaces the entry within a session's
    shared projection hash)."""

    EVT_REQUIRE = "subagent_require_user_confirm"
    """``CustomEvent.name`` used to push/replay a pending request to the
    leader's event stream."""

    EVT_RESULT = "subagent_user_confirm_result"
    """``CustomEvent.name`` used to tell the leader UI a pending request
    has been resolved and its card should be cleared."""

    def __init__(self, storage: "StorageBase") -> None:
        """Bind the storage backend.

        Args:
            storage (`StorageBase`):
                Application storage, used to resolve the team (and hence
                the leader session) a worker session belongs to.
        """
        self._storage = storage

    @staticmethod
    def entry_id(worker_session_id: str, reply_id: str) -> str:
        """Return the projection entry id for one pending request.

        Args:
            worker_session_id (`str`):
                The worker session that emitted the HITL request.
            reply_id (`str`):
                The worker-side reply id the request belongs to.

        Returns:
            `str`:
                The entry id, ``"{worker_session_id}:{reply_id}"``.
        """
        return f"{worker_session_id}:{reply_id}"

    async def maybe_project(
        self,
        user_id: str,
        session_record: "SessionRecord",
        agent_record: "AgentRecord",
        event: "AgentEvent",
        projection: "SessionProjection",
    ) -> None:
        """Mirror a worker HITL event onto its team's leader session.

        When a *worker* session emits an HITL request, write the pending
        card (durable entry + live notification) onto the leader; when
        it resolves one or its reply ends, clear the card. No-op for
        non-team sessions and for the leader session itself (a leader's
        own HITL reaches its client directly).

        Args:
            user_id (`str`):
                The owner of the running session.
            session_record (`SessionRecord`):
                The currently-running session's record.
            agent_record (`AgentRecord`):
                The currently-running agent's record. Only
                ``source == "team"`` agents forward.
            event (`AgentEvent`):
                The event just published to this session's channel.
            projection (`SessionProjection`):
                Shared primitive used to write the durable entry and the
                live notification.
        """
        # Fast path: only team-member sessions forward anything, and
        # only for the event kinds we care about.
        if agent_record.source != "team" or not session_record.team_id:
            return
        if not isinstance(
            event,
            (
                RequireUserConfirmEvent,
                RequireExternalExecutionEvent,
                UserConfirmResultEvent,
                ExternalExecutionResultEvent,
                ReplyEndEvent,
            ),
        ):
            return

        team = await self._storage.get_team(
            user_id,
            session_record.team_id,
        )
        if team is None or team.session_id == session_record.id:
            # No team, or this IS the leader session — nothing to mirror.
            return
        leader_sid = team.session_id

        if isinstance(
            event,
            (RequireUserConfirmEvent, RequireExternalExecutionEvent),
        ):
            payload = {
                "worker_session_id": session_record.id,
                "worker_agent_id": agent_record.id,
                "worker_agent_name": agent_record.data.name,
                "reply_id": event.reply_id,
                "event_type": (
                    "require_user_confirm"
                    if isinstance(event, RequireUserConfirmEvent)
                    else "require_external_execution"
                ),
                "event": event.model_dump(mode="json"),
                "created_at": datetime.now().isoformat(),
            }
            await projection.upsert(
                leader_sid,
                self.KIND,
                self.entry_id(session_record.id, event.reply_id),
                payload,
            )
            await projection.publish(leader_sid, self.EVT_REQUIRE, payload)
        else:
            # Clear the pending card. ``ReplyEndEvent`` is the primary
            # clear signal (the resume's continuation event is NOT
            # republished through the stream); the explicit result
            # events clear early when they do flow through. All are
            # idempotent — deleting an already-gone entry is a no-op.
            await projection.delete(
                leader_sid,
                self.KIND,
                self.entry_id(session_record.id, event.reply_id),
            )
            await projection.publish(
                leader_sid,
                self.EVT_RESULT,
                {
                    "worker_session_id": session_record.id,
                    "reply_id": event.reply_id,
                },
            )

    @classmethod
    async def resolve(
        cls,
        projection: "SessionProjection",
        leader_sid: str,
        reply_id: str,
    ) -> dict | None:
        """Find the pending entry for ``reply_id`` under a leader.

        Used by the confirm-routing entry point (the chat router): given
        a confirm result POSTed to the leader session, locate which
        worker session it actually belongs to so the result can be
        forwarded there.

        Args:
            projection (`SessionProjection`):
                The shared projection store to scan.
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
        for entry in await projection.list(leader_sid, cls.KIND):
            if entry.get("reply_id") == reply_id:
                return entry
        return None

    @classmethod
    async def purge(
        cls,
        projection: "SessionProjection",
        leader_sid: str,
    ) -> None:
        """Drop every pending HITL entry for a leader session.

        Used when the leader session (or its team) is deleted. Scoped to
        this feed so other projections on the same session survive.

        Args:
            projection (`SessionProjection`):
                The shared projection store.
            leader_sid (`str`):
                The leader session to purge.
        """
        await projection.purge(leader_sid, cls.KIND)

    @classmethod
    async def drop_worker(
        cls,
        projection: "SessionProjection",
        leader_sid: str,
        worker_sid: str,
    ) -> None:
        """Drop every pending entry that originated from one worker.

        Used when a single worker session is deleted while the leader
        survives.

        Args:
            projection (`SessionProjection`):
                The shared projection store.
            leader_sid (`str`):
                The leader session the entries are projected onto.
            worker_sid (`str`):
                The worker session whose entries should be dropped.
        """
        for entry in await projection.list(leader_sid, cls.KIND):
            if entry.get("worker_session_id") == worker_sid:
                await projection.delete(
                    leader_sid,
                    cls.KIND,
                    cls.entry_id(worker_sid, entry["reply_id"]),
                )
