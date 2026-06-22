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

from ..message_bus import MessageBus
from ..storage import StorageBase
from ._session_projection import SessionProjection
from ._projectors import SubagentHitlProjector
from ..._logging import logger


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
    ) -> None:
        """Bind dependencies.

        Args:
            storage (`StorageBase`): Persistent storage backend.
            message_bus (`MessageBus`): Live message bus.
        """
        self._storage = storage
        self._bus = message_bus
        self._projection = SessionProjection(message_bus)

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
        await self._bus.session_publish_cancel(session_id)

        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if not await self._bus.session_is_running(session_id):
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

        # Clean leader-side subagent HITL projections before storage
        # cascades remove the records we need to resolve roles from.
        await self._purge_subagent_hitl(user_id, agent_id, session_id)

        await self._cancel_runs(all_sids)
        deleted = await self._storage.delete_session(
            user_id,
            agent_id,
            session_id,
        )
        await self._purge_bus(all_sids)
        return deleted

    async def delete_team(self, user_id: str, team_id: str) -> bool:
        """Cancel, delete and bus-purge a team.

        Delegates worker dissolution to :meth:`delete_agent` (one call
        per ``member_id``) so the per-session cancel + bus purge runs
        for each worker. The leader's own session is **not** deleted —
        teams dissolve, leaders survive (and have their ``team_id``
        cleared by ``storage.delete_team``).

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

        for member_id in team.data.member_ids:
            await self.delete_agent(user_id, member_id)

        # storage.delete_team will iterate member_ids again to delete
        # each worker agent — those calls are now no-ops because the
        # agents are already gone, leaving only the leader-detach and
        # team-record cleanup work.
        return await self._storage.delete_team(user_id, team_id)

    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Cancel, delete and bus-purge every session and schedule
        owned by an agent, then drop the agent record.

        Delegates per-session work to :meth:`delete_session` and
        per-schedule work to :meth:`delete_schedule`, then asks
        storage to clean the remaining agent-scoped state (the agent
        record, the agent index entry, and any team back-references).

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent to delete.

        Returns:
            `bool`:
                ``True`` if the agent record existed and was deleted.
        """
        for session in await self._storage.list_sessions(user_id, agent_id):
            await self.delete_session(user_id, agent_id, session.id)

        for schedule in await self._storage.list_schedules(user_id):
            if schedule.agent_id == agent_id:
                await self.delete_schedule(user_id, schedule.id)

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
        sids: list[str] = []
        for member_id in team.data.member_ids:
            worker_sessions = await self._storage.list_sessions(
                user_id,
                member_id,
            )
            sids.extend(s.id for s in worker_sessions)
        return sids

    async def _cancel_runs(self, session_ids: list[str]) -> None:
        """Cancel every in-flight run in ``session_ids`` concurrently.

        Args:
            session_ids (`list[str]`):
                Sessions whose runs should be cancelled.
        """
        if not session_ids:
            return
        await asyncio.gather(
            *(self.cancel_session_run(sid) for sid in session_ids),
        )

    async def _purge_bus(self, session_ids: list[str]) -> None:
        """Drop bus state (events log + inbox) for each id concurrently.

        Args:
            session_ids (`list[str]`):
                Sessions whose bus state should be purged.
        """
        if not session_ids:
            return
        await asyncio.gather(
            *(self._bus.session_purge(sid) for sid in session_ids),
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
