# -*- coding: utf-8 -*-
"""The cron scheduler manager class."""
import json
from collections.abc import Callable, Coroutine

from typing import Self

from ....message import HintBlock
from ....permission import PermissionContext
from ....state import AgentState
from ....tool import ToolBase
from ...._logging import logger
from ._tools import ScheduleCreate, ScheduleDelete, ScheduleList, ScheduleView
from ...message_bus import MessageBus
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
    """

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
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
        """
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        self._storage = storage
        self._message_bus = message_bus
        self._scheduler = AsyncIOScheduler()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Start APScheduler and re-register persisted schedules.

        Reading all schedules from storage and restoring them is the
        only thing a caller would ever do right after starting this
        manager, so the work lives inside the context entry — the
        lifespan does not need to remember to call :meth:`restore`.

        Returns:
            `Self`: This manager instance.
        """
        logger.info("SchedulerManager starting APScheduler")
        self._scheduler.start()
        logger.info("SchedulerManager APScheduler started")

        records = await self._storage.list_all_schedules()
        if records:
            await self.restore(records)

        return self

    async def __aexit__(self, *exc: object) -> None:
        """Shut down the underlying APScheduler on context exit."""
        logger.info("SchedulerManager shutting down APScheduler")
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
                            workspace_id="",
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
                            workspace_id="",
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
                await message_bus.inbox_push(
                    session.id,
                    hint.model_dump(mode="json"),
                )
                await message_bus.enqueue_wakeup(
                    user_id=record.user_id,
                    session_id=session.id,
                    agent_id=record.agent_id,
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

    async def register_schedule(self, record: ScheduleRecord) -> str:
        """Persist-and-register a schedule record with APScheduler.

        Builds the trigger coroutine via :meth:`_build_trigger` and adds the
        job to APScheduler.  This is the single entry point used by both the
        HTTP API and the :class:`ScheduleCreate` agent tool.

        Args:
            record (`ScheduleRecord`):
                The fully-populated record (already persisted to storage).

        Returns:
            `str`:
                The APScheduler job ID (equal to ``record.id``).
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
        logger.info(
            "Schedule %s(%s) registered, next_run=%s",
            record.id,
            record.data.name,
            job.next_run_time,
        )
        return job.id

    async def remove_schedule(self, job_id: str) -> None:
        """Remove a job from APScheduler.

        Args:
            job_id (`str`):
                The APScheduler job ID to remove.
        """
        from apscheduler.jobstores.base import JobLookupError

        logger.info("Removing schedule job %s", job_id)
        try:
            self._scheduler.remove_job(job_id)
            logger.info("Schedule job %s removed", job_id)
        except JobLookupError:
            logger.warning("Schedule job %s not found in APScheduler", job_id)

    async def restore(self, records: list[ScheduleRecord]) -> None:
        """Re-register persisted schedules on service startup.

        Only enabled schedules are restored.

        Args:
            records (`list[ScheduleRecord]`):
                All schedule records loaded from storage on startup.
        """
        enabled = [r for r in records if r.data.enabled]
        logger.info(
            "Restoring schedules: %d total, %d enabled",
            len(records),
            len(enabled),
        )
        for record in enabled:
            await self.register_schedule(record)
        logger.info("Schedule restore complete")

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
