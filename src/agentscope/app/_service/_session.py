# -*- coding: utf-8 -*-
"""Cross-resource session lifecycle service.

Owns the "stop in-flight runs + delete records + drop bus state"
cascades that ``DELETE /sessions/{sid}``, ``DELETE /agents/{aid}``,
``DELETE /schedules/{sid}`` and the agent-facing
:class:`~agentscope.app._tools.TeamDelete` /
:class:`~agentscope.app._manager._scheduler._tools.ScheduleDelete`
tools all share.

Layering
========

Methods deliberately delegate down the cascade so the bus-touching
logic lives in exactly one place — :meth:`delete_session`. Higher-level
methods only orchestrate which sessions to delete, then ask storage
to clean its own non-session scope (records, indexes, back-refs).

::

    delete_session    ← atomic: cancel run, storage.delete_session,
                        bus.session_purge
        │
    delete_team       → service.delete_agent per worker
                      → storage.delete_team    (record + leader detach)
        │
    delete_agent      → service.delete_session per session
                      → service.delete_schedule per owned schedule
                      → storage.delete_agent   (agent record + team back-refs)
        │
    delete_schedule   → service.delete_session per spawned session
                      → storage.delete_schedule (schedule record + indexes)

Storage's own internal cascades (e.g.
``storage.delete_agent`` re-iterating sessions) become idempotent
no-ops because the records are already gone — they still execute, but
do no work and never touch the bus, so the storage layer stays
unaware of the message bus.

Separation of concerns
======================

Storage and message bus are treated as distinct backends — they may
live in different databases in the future. The service is the **only**
component that touches both in the same call. Storage code never
imports the bus; bus code never imports storage.
"""
import asyncio
import json
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from enum import StrEnum

from ..message_bus import MessageBus, MessageBusKeys
from .._bus_ops import publish_session_event
from ..storage import StorageBase
from ..workspace_manager import WorkspaceManagerBase
from ..storage._utils import _ensure_team_members
from ._session_projection import SessionProjection
from ._projectors import SubagentHitlProjector
from ..._logging import logger
from ...message import ToolCallState
from ...event import CustomEvent
from ...state import AgentState


@dataclass(frozen=True)
class SessionResetTarget:
    """Identify one session selected by a clear operation."""

    user_id: str
    agent_id: str
    session_id: str
    role: str


async def _refresh_barriers_once(
    bus: MessageBus,
    targets: list[SessionResetTarget],
    operation_id: str,
    value: str,
) -> None:
    """Create or refresh one clear operation's barrier fields."""
    for target in targets:
        await bus.registry_set(
            MessageBusKeys.session_reset_barrier(target.session_id),
            operation_id,
            value,
            ttl_secs=MessageBusKeys.SESSION_RESET_BARRIER_TTL_SECS,
        )


class SessionStatus(StrEnum):
    """The high-level status of a session, unifying cluster liveness
    (from the message bus) with the durable tool-call parking state
    (from the persisted :class:`~agentscope.state.AgentState.context`).

    Exactly one value applies at any moment — the frontend renders a
    single indicator without having to reconcile multiple boolean
    flags.

    Precedence: ``RUNNING`` is decided first from the distributed
    session-run lock; only when the session is not held by any worker
    do the ``AWAITING_*`` / ``IDLE`` values (derived from the stored
    context tail) apply. This ordering reflects the ground-truth
    hierarchy — while a worker owns the run, the live in-memory state
    supersedes the persisted snapshot, which is by definition stale
    until the run yields.

    Values:
        - ``RUNNING``: a worker somewhere in the cluster currently
          holds the session's run lease on the message bus.
        - ``IDLE``: no worker is running the session, and its
          persisted context is not parked on any pending tool call.
        - ``AWAITING_PERMISSION``: no worker is running the session,
          and the persisted context tail has at least one tool call
          waiting for user permission confirmation (HITL).
        - ``AWAITING_EXTERNAL_RESULT``: no worker is running the
          session, and the persisted context tail has at least one
          tool call dispatched to an external executor and awaiting
          its result (with no peer awaiting permission).
    """

    RUNNING = "running"
    IDLE = "idle"
    AWAITING_PERMISSION = "awaiting_permission"
    AWAITING_EXTERNAL_RESULT = "awaiting_external_result"


class SessionService:
    """Cancel in-flight chat runs and cascade-delete related records.

    The cancel side broadcasts via
    :meth:`MessageBus.session_publish_cancel`, then polls
    :meth:`MessageBus.session_is_running` until the run-lock clears or
    a timeout expires — so the implementation is multi-process and
    multi-node by construction.

    Args:
        storage (`StorageBase`):
            Persistent storage backend. Owns durable records and their
            cascades among themselves.
        message_bus (`MessageBus`):
            Live message bus. Owns transient per-session state (events
            log, inbox, run-lock, cancel channel).
    """

    _CANCEL_POLL_INTERVAL_SECS: float = 0.1
    """Interval between :meth:`MessageBus.session_is_running` polls
    while waiting for a cancelled run to release its distributed
    run-lock."""

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        workspace_manager: WorkspaceManagerBase | None = None,
    ) -> None:
        """Bind dependencies.

        Args:
            storage (`StorageBase`): Persistent storage backend.
            message_bus (`MessageBus`): Live message bus.
            workspace_manager (`WorkspaceManagerBase | None`, optional):
                Used by :meth:`delete_session` to drop the deleted
                session's workspace state. ``None`` skips that step —
                callers without a manager keep the previous behaviour.
        """
        self._storage = storage
        self._bus = message_bus
        self._workspace_manager = workspace_manager
        self._projection = SessionProjection(message_bus)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_session_status(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionStatus | None:
        """Return the unified :class:`SessionStatus` for a session.

        The status collapses two orthogonal signals into a single
        four-valued enum:

        - cluster liveness comes from the distributed session-run lock
          on the shared message bus (Redis in production) — the answer
          is cluster-wide, so any worker in the deployment holding the
          lease yields ``RUNNING`` regardless of which API replica
          serves the caller;
        - the parked state (``AWAITING_PERMISSION`` /
          ``AWAITING_EXTERNAL_RESULT`` / ``IDLE``) is derived from the
          persisted ``AgentState.context`` tail, and only applies when
          no worker owns the run.

        The ``RUNNING`` check is performed **before** loading the
        persisted context: while a worker owns the run, the live
        in-memory state supersedes the stored snapshot, and the parked
        derivation would be stale. This also saves a storage round-trip
        in the hot ``RUNNING`` case.

        .. note::
            The run lease auto-expires after
            ``MessageBusKeys.SESSION_RUN_TTL_SECS`` and is refreshed by
            the owning worker while it is actively producing events, so
            a crashed worker's session flips out of ``RUNNING`` on
            lease expiry.

        Args:
            user_id (`str`):
                The authenticated user id (ownership check).
            agent_id (`str`):
                The agent that owns the session (ownership check).
            session_id (`str`):
                The session to probe.

        Returns:
            `SessionStatus | None`:
                The unified status, or ``None`` if the session does
                not exist or is not owned by the user.
        """
        # RUNNING is checked first: while a worker owns the lease, the
        # persisted context is by definition a stale snapshot, and we
        # save a storage round-trip.
        if await self._bus.is_locked(
            MessageBusKeys.session_lock(session_id),
        ):
            return SessionStatus.RUNNING

        session = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if session is None:
            return None

        return self.derive_parked_status(session.state.context)

    @staticmethod
    def derive_parked_status(context: list) -> SessionStatus:
        """Derive the parked :class:`SessionStatus` from a persisted
        ``AgentState.context``.

        Only the tail assistant message is inspected — a paused reply
        can only park pending tool calls at the very end of the
        context. ``AWAITING_PERMISSION`` wins over
        ``AWAITING_EXTERNAL_RESULT`` when both appear on the tail: no
        submitted call can complete while a peer is still awaiting user
        confirmation, so the caller-visible status is dominated by the
        blocker.

        Callers must first ensure the session is not currently held by
        any worker (see :attr:`SessionStatus.RUNNING`) — while a worker
        owns the run, the live in-memory state supersedes the persisted
        snapshot and this function's answer is by definition stale.

        Args:
            context (`list[Msg]`):
                The persisted ``AgentState.context`` list.

        Returns:
            `SessionStatus`:
                One of ``AWAITING_PERMISSION``,
                ``AWAITING_EXTERNAL_RESULT``, or ``IDLE``.
        """
        if not context:
            return SessionStatus.IDLE
        last_msg = context[-1]
        if last_msg.role != "assistant":
            return SessionStatus.IDLE
        tool_calls = last_msg.get_content_blocks("tool_call")
        if not tool_calls:
            return SessionStatus.IDLE
        if any(tc.state == ToolCallState.ASKING for tc in tool_calls):
            return SessionStatus.AWAITING_PERMISSION
        if any(tc.state == ToolCallState.SUBMITTED for tc in tool_calls):
            return SessionStatus.AWAITING_EXTERNAL_RESULT
        return SessionStatus.IDLE

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    async def cancel_session_run(
        self,
        session_id: str,
        *,
        timeout: float = 10.0,
    ) -> bool:
        """Broadcast a session cancel and wait for the chat-run lock to
        clear.

        Publishes one cancel payload on the bus's shared cancel channel,
        unconditionally. Every process's
        :class:`~agentscope.app._manager.CancelDispatcher` reacts to the
        broadcast by cancelling whatever it locally holds for the
        session — the chat-run asyncio task **and** any background
        tasks owned by that session. The publisher does not need to
        know which worker holds which piece.

        After publishing, polls
        :meth:`MessageBus.session_is_running` until the distributed
        chat-run lock clears. Only the chat run holds a distributed
        lock; BG tasks do not, so this poll only waits for the chat
        run. Returns immediately when no chat run was active.

        Idempotent: calling on an idle session just sends a no-op
        broadcast and observes a clear lock.

        Args:
            session_id (`str`):
                The session whose chat run + BG tasks should be
                cancelled.
            timeout (`float`, defaults to ``10.0``):
                Maximum seconds to wait for the chat-run lock to
                release. On timeout the method returns ``False`` so
                callers can proceed (e.g. with cascade delete) instead
                of hanging on a process that may have died.

        Returns:
            `bool`:
                ``True`` if the chat-run lock was confirmed released
                within ``timeout`` seconds (or was never held).
                ``False`` if the lock was still held when the timeout
                expired.
        """
        await self._bus.publish(
            MessageBusKeys.session_cancel_channel(),
            {"session_id": session_id},
        )

        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if not await self._bus.is_locked(
                MessageBusKeys.session_lock(session_id),
            ):
                return True
            if asyncio.get_event_loop().time() >= deadline:
                logger.warning(
                    "Session %s did not release its run-lock within "
                    "%.1fs after cancel; proceeding anyway.",
                    session_id,
                    timeout,
                )
                return False
            await asyncio.sleep(self._CANCEL_POLL_INTERVAL_SECS)

    # ------------------------------------------------------------------
    # Delete cascades — every higher-level method delegates to
    # ``delete_session`` so the cancel + bus-purge logic exists in
    # exactly one place.
    # ------------------------------------------------------------------

    async def clear_conversation(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> tuple[str, ...]:
        """Reset one conversation, cascading from a team leader.

        The operation preserves session configuration, permissions,
        workspace contents, MCP state, and team topology. It clears only
        conversation state, persisted messages, and transient bus state.

        Team resets are an idempotent saga rather than a cross-session
        transaction. Resolved targets commit independently; after partial
        success this method raises ``RuntimeError`` without rolling back
        completed targets, and callers may safely retry the whole clear.
        """
        root = await self._storage.get_session(user_id, agent_id, session_id)
        if root is None or root.agent_id != agent_id:
            raise KeyError(f"Session {session_id!r} not found.")

        targets = [
            SessionResetTarget(user_id, agent_id, session_id, "standalone"),
        ]
        resolution_failures: list[str] = []
        is_team_clear = False
        if root.team_id is not None:
            team = await self._storage.get_team(user_id, root.team_id)
            if team is not None and team.session_id == session_id:
                is_team_clear = True
                targets[0] = SessionResetTarget(
                    user_id,
                    agent_id,
                    session_id,
                    "leader",
                )
                members = await _ensure_team_members(
                    self._storage,
                    user_id,
                    team,
                )
                for member in members:
                    record = await self._storage.get_session(
                        member.owner_id,
                        member.agent_id,
                        member.session_id,
                    )
                    if (
                        record is None
                        or record.agent_id != member.agent_id
                        or record.team_id != team.id
                    ):
                        resolution_failures.append(member.session_id)
                        # pylint: disable-next=logging-fstring-interpolation
                        logger.warning(
                            f"Skipping unresolved team member session "
                            f"{member.session_id} while clearing leader "
                            f"session {session_id}.",
                        )
                        continue
                    targets.append(
                        SessionResetTarget(
                            record.user_id,
                            member.agent_id,
                            member.session_id,
                            "worker",
                        ),
                    )

        deduped = {target.session_id: target for target in targets}
        targets = [deduped[key] for key in sorted(deduped)]
        operation_id = uuid.uuid4().hex
        barrier_value = json.dumps(
            {"root_session_id": session_id},
            ensure_ascii=False,
        )
        heartbeat_stop = asyncio.Event()

        async def _refresh_barriers() -> None:
            """Keep every target barrier alive until reset finishes."""
            while not heartbeat_stop.is_set():
                for target in targets:
                    await self._bus.registry_set(
                        MessageBusKeys.session_reset_barrier(
                            target.session_id,
                        ),
                        operation_id,
                        barrier_value,
                        ttl_secs=(
                            MessageBusKeys.SESSION_RESET_BARRIER_TTL_SECS
                        ),
                    )
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=10)
                except asyncio.TimeoutError:
                    continue

        heartbeat: asyncio.Task[None] | None = None
        try:
            await _refresh_barriers_once(
                self._bus,
                targets,
                operation_id,
                barrier_value,
            )
            heartbeat = asyncio.create_task(
                _refresh_barriers(),
                name=f"session-clear-barrier:{session_id}",
            )
            released = await asyncio.gather(
                *(
                    self.cancel_session_run(target.session_id)
                    for target in targets
                ),
            )
            if not all(released):
                raise RuntimeError("A session run did not stop in time.")

            failures = list(resolution_failures)
            completed: list[str] = []
            async with AsyncExitStack() as locks:
                for target in targets:
                    await locks.enter_async_context(
                        self._bus.acquire_lock(
                            MessageBusKeys.session_lock(target.session_id),
                            ttl_secs=MessageBusKeys.SESSION_RUN_TTL_SECS,
                        ),
                    )
                for target in targets:
                    try:
                        await self._reset_conversation_target(
                            target,
                            root_session_id=session_id,
                            scope="team" if is_team_clear else "session",
                        )
                        completed.append(target.session_id)
                    except Exception:  # pylint: disable=broad-except
                        failures.append(target.session_id)
                        logger.exception(
                            "Failed to clear session %s.",
                            target.session_id,
                        )
            if failures:
                raise RuntimeError(
                    f"Conversation reset failed for {len(failures)} "
                    f"session(s).",
                )
            return tuple(completed)
        finally:
            heartbeat_stop.set()
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            await asyncio.gather(
                *(
                    self._bus.registry_del(
                        MessageBusKeys.session_reset_barrier(
                            target.session_id,
                        ),
                        operation_id,
                    )
                    for target in targets
                ),
            )

    async def _reset_conversation_target(
        self,
        target: SessionResetTarget,
        *,
        root_session_id: str,
        scope: str,
    ) -> None:
        """Reset one already-locked target and publish its clear event."""

        async def _reset_and_publish() -> None:
            """Finish a reset after it starts despite caller cancellation."""
            existing = await self._storage.get_session(
                target.user_id,
                target.agent_id,
                target.session_id,
            )
            if existing is None or existing.agent_id != target.agent_id:
                raise KeyError(f"Session {target.session_id!r} not found.")

            state = AgentState(
                session_id=existing.id,
                permission_context=(
                    existing.state.permission_context.model_copy(deep=True)
                ),
            )
            updated = await self._storage.reset_session_conversation(
                target.user_id,
                target.agent_id,
                target.session_id,
                state,
                existing.conversation_revision,
            )

            await self._purge_subagent_hitl(
                target.user_id,
                target.agent_id,
                target.session_id,
            )
            await self._bus.queue_delete(
                MessageBusKeys.inbox(target.session_id),
            )
            await self._bus.registry_drop(
                MessageBusKeys.inbox_consumer(target.session_id),
            )
            await self._bus.registry_drop(
                MessageBusKeys.bg_tasks(target.session_id),
            )
            await self._bus.log_trim(
                MessageBusKeys.session_events(target.session_id),
            )
            event = CustomEvent(
                name="session_cleared",
                value={
                    "root_session_id": root_session_id,
                    "session_id": target.session_id,
                    "scope": scope,
                    "conversation_revision": updated.conversation_revision,
                    "tasks_context": state.tasks_context.model_dump(
                        mode="json",
                    ),
                    "permission_context": (
                        state.permission_context.model_dump(mode="json")
                    ),
                },
            )
            await publish_session_event(
                self._bus,
                target.session_id,
                event.model_dump(mode="json"),
            )

        reset_task = asyncio.create_task(_reset_and_publish())
        try:
            await asyncio.shield(reset_task)
        except asyncio.CancelledError:
            try:
                await reset_task
            except Exception:  # pylint: disable=broad-except
                # pylint: disable-next=logging-fstring-interpolation
                logger.exception(
                    f"Failed to finish session {target.session_id} after "
                    f"cancellation.",
                )
            raise

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Cancel, delete and bus-purge a single session.

        This is the atomic primitive — every other cascade delegates
        here for per-session work.

        Steps:

        1. Cancel any in-flight run for ``session_id`` (cross-process
           via the bus cancel channel).
        2. Delete the session record (and its storage-side cascade:
           message log, schedule-session index, team dissolution when
           this session leads one — recursive into worker agents).
        3. Purge transient bus state for ``session_id`` (events log,
           inbox).
        4. Drop the session's workspace state (MCP clients, offload
           files). Workspaces outlive sessions and are usually shared
           by every session of an agent, so this would otherwise
           accumulate for the life of the workspace.

        Worker sessions that storage cascades through are picked up
        here too: when this session is a team leader,
        ``storage.delete_session`` calls ``storage.delete_team`` →
        ``storage.delete_agent`` → ``storage.delete_session`` for each
        worker, and we mirror that on the bus side by purging worker
        sessions identified up front via
        :meth:`_team_worker_session_ids`.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent that owns the session.
            session_id (`str`): The session to delete.

        Returns:
            `bool`:
                ``True`` if the session record existed and was deleted,
                ``False`` otherwise. Mirrors
                :meth:`StorageBase.delete_session`.
        """
        # Identify all bus-purge targets before storage mutates anything.
        worker_sids = await self._team_worker_session_ids(
            user_id,
            agent_id,
            session_id,
        )
        all_sids = [session_id, *worker_sids]

        # Resolve the workspace binding while the record still exists.
        record = await self._storage.get_session(user_id, agent_id, session_id)
        workspace_id = record.config.workspace_id if record else None

        # Clean leader-side subagent HITL projections before storage
        # cascades remove the records we need to resolve roles from.
        await self._purge_subagent_hitl(user_id, agent_id, session_id)

        await asyncio.gather(
            *(self.cancel_session_run(sid) for sid in all_sids),
        )
        deleted = await self._storage.delete_session(
            user_id,
            agent_id,
            session_id,
        )
        await asyncio.gather(
            *(self._purge_session_bus(sid) for sid in all_sids),
        )

        # Best-effort: a workspace that cannot be resolved (backend
        # down, sandbox gone) must not fail a committed deletion.
        if deleted and self._workspace_manager and workspace_id:
            try:
                workspace = await self._workspace_manager.get_workspace(
                    user_id,
                    agent_id,
                    session_id,
                    workspace_id,
                )
                await workspace.purge_session(
                    agent_id=agent_id,
                    session_id=session_id,
                )
            except Exception as e:
                logger.warning(
                    "Failed to purge workspace %r for session %r: %s",
                    workspace_id,
                    session_id,
                    e,
                )
        return deleted

    async def delete_team(self, user_id: str, team_id: str) -> bool:
        """Cancel, delete and bus-purge a team.

        With :meth:`StorageBase.delete_team` now role-aware, the service
        method's sole responsibility is to run per-member
        ``cancel + bus purge`` (storage cannot touch the bus) via the
        role-appropriate ``delete_agent`` / ``delete_session`` call, and
        then delegate to storage for the remaining leader-detach +
        team-record cleanup.

        Branches per member role:

        - ``role == "created"``: the member was spawned by ``AgentCreate``
          and lives only as long as the team — delegate to
          :meth:`delete_agent` so its record, session, and bus state
          are fully removed.
        - ``role == "invited"``: the member is a pre-existing user-owned
          agent borrowed via ``AgentInvite``. Only the borrowed
          team-scoped session is removed; the underlying
          :class:`AgentRecord` and its other sessions survive.

        The leader's own session is **not** deleted — teams dissolve,
        leaders survive (and have their ``team_id`` cleared by
        ``storage.delete_team``).

        Args:
            user_id (`str`): The owner user id.
            team_id (`str`): The team to dissolve.

        Returns:
            `bool`:
                ``True`` if the team record existed and was deleted.
        """
        team = await self._storage.get_team(user_id, team_id)
        if team is None:
            # Still call storage.delete_team so it can clean any index
            # residue, but the return value will be False.
            return await self._storage.delete_team(user_id, team_id)

        members = await _ensure_team_members(self._storage, user_id, team)
        for member in members:
            if member.role == "created":
                await self.delete_agent(member.owner_id, member.agent_id)
            else:  # invited
                await self.delete_session(
                    member.owner_id,
                    member.agent_id,
                    member.session_id,
                )

        # storage.delete_team walks the same members again to run its
        # own role-aware cascade — those calls are now idempotent
        # no-ops (records already removed above). What remains for
        # storage is leader-detach + team-record + index cleanup.
        return await self._storage.delete_team(user_id, team_id)

    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Cancel, delete and bus-purge every session and schedule
        owned by an agent, then drop the agent record.

        Delegates per-session work to :meth:`delete_session` and
        per-schedule work to :meth:`delete_schedule`, then asks
        storage to clean the remaining agent-scoped state (the agent
        record, the agent index entry, and any team back-references).

        **Reverse-cascade for invited members.** When an agent is
        borrowed into a team (``AgentInvite``), it has a session with
        ``team_id`` set that it does not lead. Deleting the agent while
        such a borrowed session exists would leave a stale
        :class:`TeamMember` entry behind, and — worse — trigger the
        storage cascade in that team's next ``delete_team``, which
        iterates the team roster (via ``_ensure_team_members``) and
        would try to re-delete this already-gone agent. So before
        deleting each session, this method extracts the corresponding
        entry from the borrowing team's roster (matched by
        ``session_id``, not ``agent_id``, so a leader session that
        happens to share the agent id — impossible today but cheap to
        be careful about — is not touched).

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent to delete.

        Returns:
            `bool`:
                ``True`` if the agent record existed and was deleted.
        """
        # Collected before the sessions go, since a deleted session can
        # no longer tell us which workspace held the agent's skills.
        workspaces: dict[str, str] = {}
        for session in await self._storage.list_sessions(user_id, agent_id):
            if session.config.workspace_id:
                workspaces[session.config.workspace_id] = session.id
            if session.team_id is not None:
                team = await self._storage.get_team(
                    user_id,
                    session.team_id,
                )
                # Only worker sessions require the extraction — a leader
                # session dropping its team is handled by
                # ``storage.delete_session -> delete_team`` further down.
                if team is not None and team.session_id != session.id:
                    members = await _ensure_team_members(
                        self._storage,
                        user_id,
                        team,
                    )
                    filtered = [
                        m for m in members if m.session_id != session.id
                    ]
                    if len(filtered) != len(members):
                        team.data.members = filtered
                        team.data.member_ids = [
                            mid
                            for mid in team.data.member_ids
                            if mid != agent_id
                        ]
                        await self._storage.upsert_team(user_id, team)
            await self.delete_session(user_id, agent_id, session.id)

        for schedule in await self._storage.list_schedules(user_id):
            if schedule.agent_id == agent_id:
                await self.delete_schedule(user_id, schedule.id)

        # Best-effort: a workspace that cannot be resolved (backend
        # down, sandbox gone) must not fail a committed deletion.
        for workspace_id, session_id in workspaces.items():
            if not self._workspace_manager:
                break
            try:
                workspace = await self._workspace_manager.get_workspace(
                    user_id,
                    agent_id,
                    session_id,
                    workspace_id,
                )
                await workspace.purge_agent(agent_id=agent_id)
            except Exception as e:
                logger.warning(
                    "Failed to purge workspace %r for agent %r: %s",
                    workspace_id,
                    agent_id,
                    e,
                )

        # storage.delete_agent re-iterates sessions and schedules —
        # those re-runs are idempotent no-ops because the records were
        # already removed above. What remains is the agent record,
        # the agent index entry, and team back-reference scrubbing.
        return await self._storage.delete_agent(user_id, agent_id)

    async def delete_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> bool:
        """Cancel, delete and bus-purge every session spawned by a
        schedule, then drop the schedule record.

        Args:
            user_id (`str`): The owner user id.
            schedule_id (`str`): The schedule to delete.

        Returns:
            `bool`:
                ``True`` if the schedule record existed and was deleted.
        """
        for session in await self._storage.list_sessions_by_schedule(
            user_id,
            schedule_id,
        ):
            await self.delete_session(
                user_id,
                session.agent_id,
                session.id,
            )

        # storage.delete_schedule re-iterates the same sessions —
        # idempotent no-ops; only schedule record + indexes remain.
        return await self._storage.delete_schedule(user_id, schedule_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _team_worker_session_ids(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> list[str]:
        """Return the session ids of every worker in the team that
        ``session_id`` leads, or ``[]`` when the session does not lead
        a team.

        Mirrors :meth:`StorageBase.delete_session`'s own team-leader
        cascade so the bus side can purge the same sessions.

        Uses :func:`ensure_team_members` — which carries the exact
        session id per member (including invited members whose agent
        can have multiple sessions of its own, only one of which
        belongs to this team). Falling back to
        ``list_sessions(agent_id)[0]`` here would pick the wrong
        session for invited agents.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`):
                The agent that owns ``session_id``. May be empty when
                unknown; team-leader lookup does not depend on it.
            session_id (`str`):
                The candidate leader session.

        Returns:
            `list[str]`:
                Worker session ids, empty when this session is not a
                team leader.
        """
        session = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if session is None or not session.team_id:
            return []
        team = await self._storage.get_team(user_id, session.team_id)
        if team is None or team.session_id != session_id:
            return []
        members = await _ensure_team_members(self._storage, user_id, team)
        return [m.session_id for m in members]

    async def _purge_session_bus(self, session_id: str) -> None:
        """Drop all per-session bus state for one session."""
        await self._bus.log_trim(
            MessageBusKeys.session_events(session_id),
        )
        await self._bus.queue_delete(
            MessageBusKeys.inbox(session_id),
        )
        await self._bus.registry_drop(
            MessageBusKeys.bg_tasks(session_id),
        )

    async def _purge_subagent_hitl(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> None:
        """Clean leader-side subagent HITL projections for a session
        about to be deleted (design §3.7).

        Two cases, resolved from the session's role:

        - **Leader session** (it leads a team): purge the entire hash
          keyed by this session — every projected member card goes.
        - **Worker session** (it has a ``team_id`` but is not the
          leader): drop just this worker's entries from the *leader's*
          hash, leaving sibling members' cards intact.

        Must run before storage cascades remove the team / session
        records this resolution depends on. Failures are swallowed — a
        stale projection is self-healed by reconcile-on-read and must
        not block the delete cascade.

        Args:
            user_id (`str`):
                The owner user id.
            agent_id (`str`):
                The agent that owns ``session_id``.
            session_id (`str`):
                The session being deleted.
        """
        try:
            session = await self._storage.get_session(
                user_id,
                agent_id,
                session_id,
            )
            if session is None or not session.team_id:
                # Not in a team — also clear any hash that may have been
                # created with this session as a (future) leader key.
                await SubagentHitlProjector.purge(self._projection, session_id)
                return

            team = await self._storage.get_team(user_id, session.team_id)
            if team is None:
                await SubagentHitlProjector.purge(self._projection, session_id)
                return

            if team.session_id == session_id:
                # Leader session — drop the whole projection store.
                await SubagentHitlProjector.purge(self._projection, session_id)
            else:
                # Worker session — drop only its entries from the
                # leader's store.
                await SubagentHitlProjector.drop_worker(
                    self._projection,
                    team.session_id,
                    session_id,
                )
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                "Failed to purge subagent HITL projection for session "
                "%s: %s",
                session_id,
                str(e),
            )
