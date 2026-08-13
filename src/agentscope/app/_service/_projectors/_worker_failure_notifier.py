# -*- coding: utf-8 -*-
"""Projector that notifies a team leader when its worker fails.

When a *worker* session's reply ends with a non-success
``finished_reason`` (``exceed_max_iters`` / ``error``), the worker may
have stopped mid-task without ever calling ``TeamSay`` to report
results. Without intervention the leader is left waiting forever,
because the worker run terminated cleanly (from the bus's point of
view) and produced no inbox payload for the leader. The leader's
``is_running`` flips to ``false`` but the leader's system prompt has
no new context to act on.

This projector closes that gap: it inspects every
``ReplyEndEvent`` flowing through the chat service's projection
loop, and when the emitting session is a worker in a team whose
run failed, it pushes a synthesised system HintBlock onto the
leader's *inbox* and enqueues a wakeup run trigger for the
leader. The leader's next chat turn sees the failure notice as a
real ``<system-reminder>``-style hint and can decide whether to
re-delegate, abort, or escalate.

Design notes:

- The decision tree deliberately ignores ``completed`` and
  ``interrupted`` runs: a worker that finished naturally already
  reported (or chose not to); an interruption is the user's
  intent and does not need a synthetic notice.
- The pushed HintBlock is short on purpose: the leader needs the
  *fact* of failure + the worker's handle so it can re-target the
  same agent via ``AgentInvite`` / ``TeamSay`` without
  re-resolving the directory.
- Inbox + wakeup delivery is best-effort. If the team has been
  deleted or the leader session has been replaced, we log and
  skip — a stale notification is worse than none.
- This projector is stacked alongside :class:`SubagentHitlProjector`
  (built-in) and any user-supplied projectors; it is wired into
  :class:`ChatService` at construction. It needs the storage
  handle (to resolve the team) and the message bus (to enqueue
  the inbox hint + a leader wakeup).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ....event import ReplyEndEvent, ReplyFinishedReason
from ..._bus_ops import enqueue_run_trigger
from ...message_bus import MessageBusKeys

if TYPE_CHECKING:
    from ...storage import AgentRecord, SessionRecord, StorageBase
    from ._session_projection import SessionProjection


# Reasons that should bubble a synthetic notice up to the leader.
# "completed" is intentionally absent — a successfully-finished
# worker already reports back via TeamSay by design. "interrupted"
# is user-initiated and need not be relayed.
_RELAY_REASONS: frozenset[str] = frozenset(
    {
        ReplyFinishedReason.EXCEED_MAX_ITERS.value,
        ReplyFinishedReason.ERROR.value,
    },
)


def _build_hint(
    worker_agent_name: str,
    worker_session_id: str,
    worker_agent_id: str,
    reply_id: str,
    finished_reason: str,
    error_summary: str | None,
) -> str:
    """Format the synthetic system hint string.

    Kept terse and machine-actionable so the leader's reasoning
    step at the next turn can decide without re-parsing.
    """
    reason_label = {
        ReplyFinishedReason.EXCEED_MAX_ITERS.value: (
            "exceeded max iterations"
        ),
        ReplyFinishedReason.ERROR.value: "errored",
    }.get(finished_reason, finished_reason)
    head = (
        f"[team-worker-run-status] Your team member "
        f"'{worker_agent_name}' (session={worker_session_id}) "
        f"just **{reason_label}** and did NOT report a result. "
    )
    if error_summary:
        head += f"Reason: {error_summary}. "
    head += (
        f"You can re-delegate the task via "
        f"``AgentInvite(target='{worker_agent_name}@"
        f"{worker_agent_id[:8]}')`` or instruct a different "
        "member. Reply ``ack`` to dismiss."
    )
    return head


class WorkerFailureNotifier:
    """Bubble worker run failures to the team leader's inbox.

    Wraps both the generic :class:`SessionProjection` (used to
    surface the failure as a UI feed card on the leader session)
    *and* the low-level message bus primitives needed to enqueue a
    :class:`HintBlock` on the leader's inbox plus a wakeup run
    trigger. The latter two are not projection material — they
    shape the leader's *next reasoning turn* — so they bypass the
    projection primitive deliberately.
    """

    KIND = "worker_failure_notice"
    """Projection feed key, namespacing the entry within a
    session's shared projection hash. Kept distinct from
    :attr:`SubagentHitlProjector.KIND` so the two feeds never
    collide on the same leader."""

    EVT_NOTICE = "worker_run_failed"
    """``CustomEvent.name`` used to push a failure notice onto a
    live leader UI subscribing through the SSE channel."""

    def __init__(self, storage: "StorageBase") -> None:
        """Bind the storage backend.

        Args:
            storage (`StorageBase`): Application storage, used to
                resolve a worker's team and leader.

        Note:
            The bus is taken from
            :meth:`AgentRecord.app` / :meth:`SessionRecord.app`
            at invocation time so that the projector remains
            bound even when constructed before the bus is
            attached elsewhere.
        """
        self._storage = storage

    async def maybe_project(
        self,
        user_id: str,
        session_record: "SessionRecord",
        agent_record: "AgentRecord",
        event: "ReplyEndEvent",
        projection: "SessionProjection",
    ) -> None:
        """Decide whether this end-of-reply needs to wake the leader.

        Args:
            user_id (`str`): Owner of the running session.
            session_record (`SessionRecord`): The session that just
                ended. Its ``team_id`` decides whether this is a
                worker run (it carries one) versus a leader run
                (whose own session id is the team's session id).
            agent_record (`AgentRecord`): The currently-running
                agent. Used to render the leader-facing message
                with the worker's display name + handle.
            event (`ReplyEndEvent`): The reply-end event freshly
                produced by the worker session. Its
                ``finished_reason`` is the gating signal.
            projection (`SessionProjection`): Generic projection
                primitive used to surface the failure as a UI
                card on the leader session.
        """
        # 1. Session gating — only team sessions are interesting.
        if not session_record.team_id:
            return

        # 2. Reason gating — only failure reasons need synthetic
        #    notice. ``completed`` workers are assumed to have
        #    reported via TeamSay; ``interrupted`` runs are
        #    user-initiated and need not be relayed.
        finished_reason = getattr(event, "finished_reason", None)
        if finished_reason is None:
            return
        finished_reason_str = str(finished_reason)
        if finished_reason_str not in _RELAY_REASONS:
            return

        # 3. Team gating — ignore leader sessions and orphaned teams.
        team = await self._storage.get_team(
            user_id,
            session_record.team_id,
        )
        if team is None or team.session_id == session_record.id:
            return
        leader_sid = team.session_id

        # 4. Pull a short error summary when one is present.
        error_info = getattr(event, "error", None)
        error_summary: str | None = None
        if error_info is not None:
            msg = getattr(error_info, "message", None)
            err_type = getattr(error_info, "type", None)
            if msg and err_type:
                error_summary = f"{err_type}: {msg}"
            elif msg:
                error_summary = str(msg)
            elif err_type:
                error_summary = str(err_type)

        hint_text = _build_hint(
            worker_agent_name=agent_record.data.name,
            worker_session_id=session_record.id,
            worker_agent_id=agent_record.id,
            reply_id=event.reply_id,
            finished_reason=finished_reason_str,
            error_summary=error_summary,
        )
        payload = {
            "worker_session_id": session_record.id,
            "worker_agent_id": agent_record.id,
            "worker_agent_name": agent_record.data.name,
            "reply_id": event.reply_id,
            "finished_reason": finished_reason_str,
            "error_summary": error_summary,
            "hint": hint_text,
        }
        entry_id = (
            f"{session_record.id}:{event.reply_id}:"
            f"{finished_reason_str}"
        )

        # 5a. Project onto the leader's UI feed — both durable
        #     (registry hash) and live (CustomEvent). Done first
        #     so a downstream bus failure doesn't suppress the
        #     UI affordance.
        try:
            await projection.upsert(
                leader_sid,
                self.KIND,
                entry_id,
                payload,
            )
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            await projection.publish(
                leader_sid,
                self.EVT_NOTICE,
                payload,
            )
        except Exception:  # pylint: disable=broad-except
            pass

        # 5b. Push a HintBlock onto the leader's inbox so the next
        #     chat turn reads it as a real system-reminder rather
        #     than a user message. We use the queue_push
        #     primitive directly because no SessionProjection
        #     helper exists for generic inbox payloads and adding
        #     one would couple this feature to the projection
        #     protocol incorrectly.
        bus = getattr(projection, "_bus", None)
        if bus is None:
            # Fall back — the bus is sometimes exposed via the
            # session record directly.
            bus = getattr(session_record, "app", None)
        if bus is None:
            return
        try:
            await bus.queue_push(
                MessageBusKeys.inbox(leader_sid),
                {
                    "type": "hint",
                    "hint": hint_text,
                    "source": '{"label": "System",'
                    ' "sublabel": "WorkerFailureNotifier"}',
                },
            )
        except Exception:  # pylint: disable=broad-except
            pass

        # 5c. Schedule a leader wakeup so the synthetic notice
        #     is actually consumed by a chat turn. The
        #     ``wake`` kind drains the inbox queue on the
        #     next dispatch tick; ``resume`` is reserved for
        #     HITL/HITL-like flows that carry an input event.
        try:
            await enqueue_run_trigger(
                bus,
                user_id=user_id,
                session_id=leader_sid,
                agent_id=agent_record.id,  # best-known agent id
            )
        except Exception:  # pylint: disable=broad-except
            pass
