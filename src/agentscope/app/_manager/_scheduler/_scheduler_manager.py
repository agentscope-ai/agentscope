# -*- coding: utf-8 -*-
"""The cron scheduler manager class."""
import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import datetime

from typing import Self

from ....message import HintBlock
from ....permission import PermissionContext
from ....state import AgentState
from ....tool import ToolBase
from ...._logging import logger
from ...._utils._common import _generate_id
from ._tools import ScheduleCreate, ScheduleDelete, ScheduleList, ScheduleView
from ...message_bus import MessageBus, MessageBusKeys
from ...workspace_manager import WorkspaceManagerBase
from ..._bus_ops import deliver_to_inbox
from ...storage import (
    StorageBase,
    ScheduleRecord,
    ChatModelConfig,
    SessionConfig,
    SessionSource,
)


class SchedulerManager:
    """The cron scheduler manager, responsible for managing scheduled-task
    lifecycle within the agent service.

    The manager owns both the in-memory APScheduler instance and the trigger
    logic that fires scheduled tasks. Triggers do not call ``ChatService``
    directly; instead they push a :class:`HintBlock` to the target session's
    inbox and enqueue a wakeup, so that the application-wide
    :class:`WakeupDispatcher` (running on any process) picks up the work.
    This keeps the scheduler decoupled from ``ChatService`` and makes the
    fire path consistent with team / background-tool result delivery.

    The timers themselves are held by **one** node — APScheduler's
    jobstore is in-memory, so every node holding them fires every cron
    tick, and a schedule runs once per replica. Which node owns them is
    a deployment choice (``create_app(enable_scheduler=...)``), so
    writers cannot register a job in-process: they persist the record
    and call :meth:`notify_changed`, and the owner reconciles its jobs
    against storage. Storage is the source of truth; the notification
    only makes the owner look sooner than its periodic reconcile would.
    """

    RECONCILE_INTERVAL_SECS = 60
    """How often the owner re-reads storage regardless of
    notifications, so a dropped one costs at most one interval."""

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        workspace_manager: WorkspaceManagerBase,
        enabled: bool = True,
    ) -> None:
        """Initialize the scheduler manager.

        Args:
            storage (`StorageBase`):
                The storage backend used for persistence and session
                creation.
            message_bus (`MessageBus`):
                The application message bus. Each scheduled fire pushes
                a :class:`HintBlock` to the target session's inbox and
                enqueues a wakeup via this bus.
            workspace_manager (`WorkspaceManagerBase`):
                Binds a workspace to the sessions this manager creates,
                under the application's isolation policy.
            enabled (`bool`, defaults to ``True``):
                Whether this node owns the timers. Exactly one node in a
                deployment should enable them; the rest still create,
                edit and delete schedules — they just do not fire them.
        """
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        self._storage = storage
        self._message_bus = message_bus
        self._workspace_manager = workspace_manager
        self._enabled = enabled
        self._scheduler = AsyncIOScheduler()
        # ``updated_at`` of each registered job, so a reconcile can tell
        # an edited schedule from an unchanged one.
        self._versions: dict[str, datetime] = {}
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Take ownership of the timers, unless this node is disabled.

        A disabled manager holds no jobs and runs no loops; it stays
        usable for :meth:`notify_changed` and :meth:`list_tools`, which
        every node needs.

        Returns:
            `Self`: This manager instance.
        """
        if not self._enabled:
            logger.info(
                "SchedulerManager disabled on this node; schedules are "
                "owned elsewhere.",
            )
            return self

        logger.info("SchedulerManager starting APScheduler")
        self._scheduler.start()
        # Subscribe before the first reconcile, and wait for it: a
        # notification published in between would otherwise be lost, and
        # the schedule behind it would wait for the periodic pass.
        ready = asyncio.Event()
        self._tasks = [
            asyncio.create_task(
                self._listen(ready),
                name="schedule-lifecycle",
            ),
            asyncio.create_task(self._periodic(), name="schedule-reconcile"),
        ]
        await ready.wait()
        await self.reconcile()
        logger.info("SchedulerManager APScheduler started")
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop the loops and shut down APScheduler, if it was started."""
        if not self._enabled:
            return

        logger.info("SchedulerManager shutting down APScheduler")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._scheduler.shutdown()
        logger.info("SchedulerManager APScheduler shut down")

    # ------------------------------------------------------------------
    # Trigger construction
    # ------------------------------------------------------------------

    def _build_trigger(
        self,
        record: ScheduleRecord,
    ) -> Callable[[], Coroutine]:
        """Build the zero-argument coroutine executed by APScheduler on each
        trigger fire.

        The returned coroutine:

        1. Skips execution when the schedule is disabled.
        2. Resolves or creates the target session (stateful reuses a fixed
           session; non-stateful creates a fresh one on every fire).
        3. Calls :class:`~agentscope.app._service._chat.ChatService` and
           drains the response stream (fire-and-forget).
        4. Catches and logs all exceptions to prevent APScheduler from
           removing the job on failure.

        Args:
            record (`ScheduleRecord`):
                The persisted schedule record that describes what to run.

        Returns:
            `Callable[[], Coroutine]`:
                A zero-argument async callable suitable for APScheduler.
        """
        # Closure-friendly references so APScheduler doesn't have to
        # re-look these up on every fire.
        storage = self._storage
        message_bus = self._message_bus
        workspace_manager = self._workspace_manager

        async def _trigger() -> None:
            logger.info(
                "[Schedule:%s(%s)] Trigger fired",
                record.id,
                record.data.name,
            )

            if not record.data.enabled:
                logger.info(
                    "[Schedule:%s(%s)] Skipped — schedule disabled",
                    record.id,
                    record.data.name,
                )
                return

            try:
                if record.data.stateful:
                    stateful_session_id = f"{record.id}_stateful"
                    logger.info(
                        "[Schedule:%s(%s)] Stateful mode, "
                        "looking up session %s",
                        record.id,
                        record.data.name,
                        stateful_session_id,
                    )
                    session = await storage.get_session(
                        record.user_id,
                        record.agent_id,
                        stateful_session_id,
                    )
                    if session is None:
                        logger.info(
                            "[Schedule:%s(%s)] First fire, "
                            "creating stateful session",
                            record.id,
                            record.data.name,
                        )
                        state = AgentState()
                        state.permission_context = PermissionContext(
                            mode=record.data.permission_mode,
                        )
                        session_config = SessionConfig(
                            workspace_id=(
                                await workspace_manager.assign_workspace_id(
                                    user_id=record.user_id,
                                    agent_id=record.agent_id,
                                    session_id=stateful_session_id,
                                )
                            ),
                            chat_model_config=record.data.chat_model_config,
                        )
                        session = await storage.upsert_session(
                            user_id=record.user_id,
                            agent_id=record.agent_id,
                            config=session_config,
                            state=state,
                            session_id=stateful_session_id,
                            source=SessionSource.SCHEDULE,
                            source_schedule_id=record.id,
                        )
                    else:
                        logger.info(
                            "[Schedule:%s(%s)] Reusing existing "
                            "stateful session %s",
                            record.id,
                            record.data.name,
                            session.id,
                        )
                else:
                    logger.info(
                        "[Schedule:%s(%s)] Non-stateful mode, "
                        "creating fresh session",
                        record.id,
                        record.data.name,
                    )
                    state = AgentState()
                    state.permission_context = PermissionContext(
                        mode=record.data.permission_mode,
                    )
                    session = await storage.upsert_session(
                        user_id=record.user_id,
                        agent_id=record.agent_id,
                        config=SessionConfig(
                            workspace_id=(
                                await workspace_manager.assign_workspace_id(
                                    user_id=record.user_id,
                                    agent_id=record.agent_id,
                                    session_id=_generate_id(),
                                )
                            ),
                            chat_model_config=record.data.chat_model_config,
                        ),
                        state=state,
                        source=SessionSource.SCHEDULE,
                        source_schedule_id=record.id,
                    )

                logger.info(
                    "[Schedule:%s(%s)] Session ready: %s, "
                    "delivering prompt via inbox + wakeup",
                    record.id,
                    record.data.name,
                    session.id,
                )

                # Wrap the schedule prompt in an XML tag so the LLM
                # recognises it as a system-driven trigger rather than
                # a regular user turn — same shape as team / system
                # notification hints.
                hint = HintBlock(
                    hint=(
                        f"<scheduled-task>\n"
                        f"{record.data.description}\n"
                        f"</scheduled-task>"
                    ),
                    source=json.dumps(
                        {
                            "label": "schedule",
                            "sublabel": record.data.name,
                        },
                        ensure_ascii=False,
                    ),
                )
                await deliver_to_inbox(
                    message_bus,
                    user_id=record.user_id,
                    session_id=session.id,
                    agent_id=record.agent_id,
                    payload=hint.model_dump(mode="json"),
                )

                logger.info(
                    "[Schedule:%s(%s)] Wakeup enqueued for session %s",
                    record.id,
                    record.data.name,
                    session.id,
                )

            except Exception:
                logger.exception(
                    "[Schedule:%s(%s)] Trigger failed",
                    record.id,
                    record.data.name,
                )

        return _trigger

    # ------------------------------------------------------------------
    # Schedule management
    # ------------------------------------------------------------------

    async def notify_changed(self, schedule_id: str) -> None:
        """Tell the timer-owning node that a schedule was written.

        Call after persisting a create / update / delete. Best-effort:
        reconcile re-reads storage, so the payload is only a nudge and a
        lost notification costs at most one reconcile interval.

        Args:
            schedule_id (`str`):
                The changed schedule, for logging on the owner's side.
        """
        try:
            await self._message_bus.publish(
                MessageBusKeys.schedule_lifecycle(),
                {"schedule_id": schedule_id},
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Failed to publish schedule change %s; the periodic "
                "reconcile will pick it up.",
                schedule_id,
            )

    async def reconcile(self) -> None:
        """Drive the local job set to match the enabled records.

        Adds jobs that are missing, drops jobs whose record is gone or
        no longer enabled, and re-registers those whose ``updated_at``
        moved. Safe to call repeatedly — that is how a dropped
        notification heals.
        """
        try:
            records = await self._storage.list_all_schedules()
        except Exception:  # pylint: disable=broad-except
            logger.exception("Schedule reconcile: failed to list schedules")
            return

        desired = {r.id: r for r in records if r.data.enabled}

        for schedule_id in set(self._versions) - set(desired):
            self._remove_job(schedule_id)

        for schedule_id, record in desired.items():
            if self._versions.get(schedule_id) == record.updated_at:
                continue
            if schedule_id in self._versions:
                self._remove_job(schedule_id)
            try:
                self._add_job(record)
            except Exception:  # pylint: disable=broad-except
                # A record with an unparseable cron would otherwise take
                # every schedule after it down with it.
                logger.exception(
                    "Schedule reconcile: cannot register %s",
                    schedule_id,
                )

    # -- Loops --

    async def _listen(self, ready: asyncio.Event) -> None:
        """Reconcile on each lifecycle notification (reconnect on drop).

        Args:
            ready (`asyncio.Event`):
                Signalled once the SUBSCRIBE has landed, so
                :meth:`__aenter__` can order its first reconcile after
                it.
        """
        backoff = 1.0
        while True:
            try:
                async for _ in self._message_bus.subscribe(
                    MessageBusKeys.schedule_lifecycle(),
                    on_ready=ready.set,
                ):
                    backoff = 1.0
                    await self.reconcile()
            except asyncio.CancelledError:  # pylint: disable=try-except-raise
                raise
            except Exception:  # pylint: disable=broad-except
                logger.warning("schedule lifecycle subscription lost")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _periodic(self) -> None:
        """Reconcile on a fixed interval, self-healing lost events."""
        while True:
            await asyncio.sleep(self.RECONCILE_INTERVAL_SECS)
            await self.reconcile()

    # -- Job set --

    def _add_job(self, record: ScheduleRecord) -> None:
        """Add one APScheduler job from its record.

        Args:
            record (`ScheduleRecord`):
                An enabled schedule record.
        """

        from apscheduler.triggers.cron import CronTrigger

        logger.info(
            "Registering schedule %s(%s) cron=%s tz=%s",
            record.id,
            record.data.name,
            record.data.cron_expression,
            record.data.timezone,
        )

        # ``CronTrigger.from_crontab`` is a thin helper that only forwards
        # the 5 parsed fields and ``timezone`` — it has no parameter for
        # ``start_date`` / ``end_date``.  Parse the expression ourselves so
        # the configured activation window is honoured.
        fields = record.data.cron_expression.split()
        if len(fields) != 5:
            raise ValueError(
                "Expected a 5-field cron expression, got "
                f"{record.data.cron_expression!r}",
            )
        minute, hour, day, month, day_of_week = fields

        trigger = self._build_trigger(record)
        job = self._scheduler.add_job(
            trigger,
            trigger=CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
                timezone=record.data.timezone,
                start_date=record.data.started_at,
                end_date=record.data.ended_at,
            ),
            id=record.id,
            name=record.data.name,
            misfire_grace_time=300,
        )
        self._versions[record.id] = record.updated_at
        logger.info(
            "Schedule %s(%s) registered, next_run=%s",
            record.id,
            record.data.name,
            job.next_run_time,
        )

    def _remove_job(self, schedule_id: str) -> None:
        """Drop one APScheduler job.

        Args:
            schedule_id (`str`):
                The schedule whose job should go; it doubles as the
                APScheduler job id.
        """
        from apscheduler.jobstores.base import JobLookupError

        self._versions.pop(schedule_id, None)
        try:
            self._scheduler.remove_job(schedule_id)
            logger.info("Schedule job %s removed", schedule_id)
        except JobLookupError:
            logger.warning(
                "Schedule job %s not found in APScheduler",
                schedule_id,
            )

    async def list_tasks(self) -> list[dict]:
        """Return a summary of all currently registered APScheduler jobs.

        Returns:
            `list[dict]`:
                Each entry contains ``id``, ``name``, and ``next_run``.
        """
        return [
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time,
            }
            for job in self._scheduler.get_jobs()
        ]

    # ------------------------------------------------------------------
    # Agent tools
    # ------------------------------------------------------------------

    async def list_tools(
        self,
        user_id: str,
        agent_id: str,
        chat_model_config: ChatModelConfig,
    ) -> list[ToolBase]:
        """Return the agent-facing tools provided by the scheduler manager.

        Args:
            user_id (`str`):
                The authenticated user who owns the schedules.
            agent_id (`str`):
                The agent that will be run by newly created schedules.
            chat_model_config (`ChatModelConfig`):
                Model configuration inherited from the current session and
                stored on new :class:`~...ScheduleRecord` objects.

        Returns:
            `list[ToolBase]`:
                The four schedule tools: :class:`ScheduleCreate`,
                :class:`ScheduleView`, :class:`ScheduleDelete`, and
                :class:`ScheduleList`.
        """
        return [
            ScheduleCreate(
                user_id=user_id,
                agent_id=agent_id,
                chat_model_config=chat_model_config,
                storage=self._storage,
                scheduler_manager=self,
            ),
            ScheduleView(
                user_id=user_id,
                scheduler=self._scheduler,
                storage=self._storage,
            ),
            ScheduleDelete(
                user_id=user_id,
                scheduler=self._scheduler,
                storage=self._storage,
                message_bus=self._message_bus,
            ),
            ScheduleList(
                user_id=user_id,
                scheduler=self._scheduler,
                storage=self._storage,
            ),
        ]
