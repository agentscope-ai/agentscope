# -*- coding: utf-8 -*-
"""The cron scheduler manager class."""
from collections.abc import Callable, Coroutine

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ....tool import ToolBase
from ._tools import ScheduleCreate, ScheduleList, ScheduleStop, ScheduleView
from ...storage._base import StorageBase
from ...storage._model._schedule import ScheduleRecord
from ...storage._model._session import ChatModelConfig
from .._session_manager import SessionManager


class SchedulerManager:
    """The cron scheduler manager, responsible for managing scheduled-task
    lifecycle within the agent service.

    The manager owns both the in-memory APScheduler instance and the trigger
    logic that runs agents on schedule.  Inject it with ``storage`` and
    ``session_manager`` so it can build self-contained trigger coroutines
    without external callbacks.
    """

    def __init__(
        self,
        storage: StorageBase,
        session_manager: SessionManager,
    ) -> None:
        """Initialize the scheduler manager.

        Args:
            storage (`StorageBase`):
                The storage backend used for persistence and session creation.
            session_manager (`SessionManager`):
                The session manager used when running agent chat sessions.
        """
        self._storage = storage
        self._session_manager = session_manager
        self._scheduler = AsyncIOScheduler()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the underlying APScheduler."""
        self._scheduler.start()

    async def shutdown(self) -> None:
        """Shut down the underlying APScheduler."""
        self._scheduler.shutdown()

    # ------------------------------------------------------------------
    # Trigger construction
    # ------------------------------------------------------------------

    def _build_trigger(self, record: ScheduleRecord) -> Callable[[], Coroutine]:
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
        # Collect closure-friendly references to avoid re-looking them up
        # on every fire.  ChatService is imported lazily inside the closure to
        # break the circular dependency:
        #   _manager._scheduler → _service._chat → _manager
        storage = self._storage
        session_manager = self._session_manager

        async def _trigger() -> None:
            if not record.data.enable:
                return

            # Lazy import to break circular dependency
            from ..._service._chat import ChatService  # noqa: PLC0415
            from ...._logging import logger  # noqa: PLC0415
            from ...permission._context import PermissionContext  # noqa: PLC0415
            from ...state import AgentState  # noqa: PLC0415
            from ...storage._model._session import (  # noqa: PLC0415
                SessionConfig,
                SessionSource,
            )

            try:
                if record.data.stateful:
                    # Reuse a fixed session across all fires so conversation
                    # context accumulates between executions.
                    stateful_session_id = f"{record.id}_stateful"
                    session = await storage.get_session(
                        record.user_id,
                        record.agent_id,
                        stateful_session_id,
                    )
                    if session is None:
                        # First fire — create the session with the configured
                        # permission mode.
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
                            session_id=stateful_session_id,
                            source=SessionSource.SCHEDULE,
                        )
                else:
                    # Create a brand-new session for each trigger fire so
                    # every execution starts from a clean state.
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
                    )

                # TODO: assemble input message from record
                input_msg = None

                # Drain the stream; events are published to the session bus
                # and persisted by ChatService internally.
                chat_service = ChatService(
                    storage=storage,
                    session_manager=session_manager,
                )
                async for _ in chat_service.stream_chat(
                    user_id=record.user_id,
                    session_id=session.id,
                    agent_id=record.agent_id,
                    input_msg=input_msg,
                ):
                    pass

            except Exception:
                logger.exception(
                    "Schedule %r (%r) trigger failed",
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
        trigger = self._build_trigger(record)
        job = self._scheduler.add_job(
            trigger,
            trigger=CronTrigger.from_crontab(
                record.data.cron_expression,
                timezone=record.data.timezone,
            ),
            id=record.id,
            name=record.data.name,
        )
        return job.id

    async def remove_schedule(self, job_id: str) -> None:
        """Remove a job from APScheduler.

        Args:
            job_id (`str`):
                The APScheduler job ID to remove.
        """
        from apscheduler.jobstores.base import JobLookupError

        try:
            self._scheduler.remove_job(job_id)
        except JobLookupError:
            pass

    async def restore(self, records: list[ScheduleRecord]) -> None:
        """Re-register persisted schedules on service startup.

        Only enabled schedules are restored.

        Args:
            records (`list[ScheduleRecord]`):
                All schedule records loaded from storage on startup.
        """
        for record in records:
            if record.data.enable:
                await self.register_schedule(record)

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
                :class:`ScheduleView`, :class:`ScheduleStop`, and
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
            ScheduleStop(
                user_id=user_id,
                scheduler=self._scheduler,
                storage=self._storage,
            ),
            ScheduleList(
                user_id=user_id,
                scheduler=self._scheduler,
                storage=self._storage,
            ),
        ]
