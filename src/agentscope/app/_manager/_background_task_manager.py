# -*- coding: utf-8 -*-
"""The background task manager."""
import asyncio
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Self, TYPE_CHECKING

import shortuuid
from pydantic import BaseModel, ConfigDict, Field

from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from ...tool import ToolBase, ToolChunk, ToolResponse
from ..._logging import logger
from ..message_bus import MessageBusKeys

if TYPE_CHECKING:
    from ..message_bus import MessageBus


class TaskStatus(str, Enum):
    """Status of a background task throughout its lifecycle."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_MAX_RETAINED_TASKS = 50
# Maximum number of terminal-state tasks retained per user.
_RECORD_LEASE_SECS = 120
_IO_TIMEOUT_SECS = 2.0
_RETRY_DELAY_SECS = 1.0
_SHUTDOWN_TIMEOUT_SECS = 5.0


@dataclass
class BackgroundTask:
    """Metadata for a single background task.

    Attributes:
        asyncio_task (`asyncio.Task`):
            The running asyncio task.
        session_id (`str`):
            The session id of the originating request.
        agent_id (`str`):
            The name of the agent that created the task.
        user_id (`str`):
            The user id of the originating request.
        tool_name (`str`):
            The name of the tool that was offloaded.
        id (`str`):
            Auto-generated unique task identifier.
        summary (`str`):
            A short human-readable summary of the tool invocation
            for frontend display (at most 128 characters).
        started_at (`float`):
            Unix epoch (UTC) when the task was registered.
        completed_at (`float | None`):
            Unix epoch (UTC) when the task reached a terminal state,
            or ``None`` while still running.
        status (`TaskStatus`):
            Current lifecycle status of the task.
        error_summary (`str | None`):
            Brief description of the failure reason when
            ``status == FAILED``.
    """

    asyncio_task: asyncio.Task
    """The running asyncio task."""

    session_id: str
    """The session id of the background task."""

    agent_id: str
    """The agent that created the background task."""

    user_id: str
    """The user id of the originating request."""

    tool_name: str
    """The name of the offloaded tool."""

    id: str = field(default_factory=shortuuid.uuid)
    """The background task id."""

    summary: str = ""
    """Short human-readable summary for frontend display."""

    started_at: float = field(default_factory=time.time)
    """Unix epoch (UTC) when the task was registered."""

    completed_at: float | None = None
    """Unix epoch (UTC) when the task finished, or None if running."""

    status: TaskStatus = TaskStatus.RUNNING
    """Current lifecycle status."""

    error_summary: str | None = None
    """Brief failure description (populated only for FAILED tasks)."""


class BackgroundTaskRecord(BaseModel):
    """Serializable task snapshot, independent of the local task handle."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    agent_id: str
    user_id: str
    tool_name: str
    summary: str
    started_at: float
    completed_at: float | None
    status: TaskStatus
    error_summary: str | None


class _ToolStopParams(BaseModel):
    """The params of the stop tool."""

    task_id: str = Field(
        description="The task id of the background tool to stop.",
    )


class ToolStop(ToolBase):
    """A tool to stop a running background tool execution."""

    name: str = "ToolStop"
    """The tool name."""

    description: str = (
        "Stop a background tool execution by its task id. "
        "Use this when you want to cancel a previously offloaded tool "
        "that is still running in the background."
    )
    """The tool description."""

    input_schema: dict = _ToolStopParams.model_json_schema()
    """The input schema."""

    is_concurrency_safe: bool = True
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        background_tasks: dict[str, BackgroundTask],
        message_bus: "MessageBus",
        session_id: str,
    ) -> None:
        """Initialize the ToolStop tool.

        Args:
            background_tasks (`dict[str, BackgroundTask]`):
                A reference to the local background tasks managed by
                the :class:`BackgroundTaskManager`.
            message_bus (`MessageBus`):
                The application message bus, used to check the global
                registry and broadcast cross-worker cancel requests.
            session_id (`str`):
                The current session id, used to scope Redis registry
                lookups.
        """
        self.background_tasks = background_tasks
        self._message_bus = message_bus
        self._session_id = session_id

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permission for the tool usage.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input parameters.
            context (`PermissionContext`):
                The permission context.

        Returns:
            `PermissionDecision`:
                Always returns ALLOW.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is always allowed to be called.",
        )

    async def __call__(self, task_id: str) -> ToolChunk:
        """Stop the background task.

        Args:
            task_id (`str`):
                The task id.

        Returns:
            `ToolChunk`:
                The tool chunk.
        """
        # Path 1: task is on this worker — cancel directly.
        # Only cancel when the task belongs to the same session as this
        # ToolStop instance, so a leaked/guessed task_id from another
        # session cannot trigger cross-session cancellation on a shared
        # worker.
        local_task = self.background_tasks.get(task_id)
        if (
            local_task is not None
            and local_task.session_id == self._session_id
            and not local_task.asyncio_task.done()
        ):
            local_task.asyncio_task.cancel()
            logger.info(
                "Background task stopped via ToolStop (local): task_id=%s, "
                "session_id=%s, agent_id=%s",
                task_id,
                local_task.session_id,
                local_task.agent_id,
            )
            return ToolChunk(
                content=[
                    TextBlock(text=f"Task {task_id} stopped successfully."),
                ],
                state=ToolResultState.SUCCESS,
            )

        # Path 2: task exists in the global registry (another worker, or
        # a different session on this worker).
        if await self._message_bus.registry_exists(
            MessageBusKeys.bg_tasks(self._session_id),
            task_id,
        ):
            await self._message_bus.publish(
                MessageBusKeys.task_cancel_channel(),
                {"task_id": task_id},
            )
            logger.info(
                "Background task cancel broadcast via ToolStop (remote): "
                "task_id=%s, session_id=%s",
                task_id,
                self._session_id,
            )
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Cancel request sent for task {task_id}. "
                        f"The owning worker will stop it shortly.",
                    ),
                ],
                state=ToolResultState.SUCCESS,
            )

        # Path 3: task not found anywhere.
        return ToolChunk(
            content=[
                TextBlock(
                    text=f"TaskNotFoundError: The task {task_id} "
                    f"does not exist.",
                ),
            ],
            state=ToolResultState.ERROR,
        )


class BackgroundTaskManager:
    """Tracks background asyncio task lifecycle within the agent service.

    Responsibilities:

    - **Global registry**: register/unregister tasks in Redis so any
      process can query which tasks are alive for a session.
    - **Local handle cache**: hold ``asyncio.Task`` references for
      cancel and shutdown.
    - **Task scheduling**: convenience method for creating a task from
      a plain coroutine with a done callback that cleans up both sides.

    Completion results are delivered via the :class:`MessageBus` inbox
    + wakeup path (same as team messages), so any process's
    :class:`WakeupDispatcher` can pick up the result.
    """

    def __init__(self, message_bus: "MessageBus") -> None:
        """Initialise the background task manager.

        Args:
            message_bus (`MessageBus`):
                The application message bus; used for the global BG
                task registry (Redis Hash) and task-level cancel
                broadcasts.
        """
        self._message_bus = message_bus
        self.tasks: OrderedDict[str, BackgroundTask] = OrderedDict()
        self._completion_tasks: set[asyncio.Task] = set()
        self._renewal_task: asyncio.Task | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # Task registration
    # ------------------------------------------------------------------

    async def register_task(
        self,
        asyncio_task: asyncio.Task,
        session_id: str,
        agent_id: str,
        user_id: str,
        tool_name: str = "",
        *,
        summary: str = "",
    ) -> str:
        """Register an already-running asyncio task.

        Tracks running handles locally and publishes metadata globally.
        On completion, releases the handle and retains terminal metadata
        for polling from any worker.

        Args:
            asyncio_task (`asyncio.Task`):
                The already-running task to register.
            session_id (`str`):
                The originating session id.
            agent_id (`str`):
                The agent record id that owns the task.
            user_id (`str`):
                The user id of the originating request.
            tool_name (`str`, optional):
                The name of the offloaded tool.
            summary (`str`, optional):
                Short human-readable summary of the tool invocation
                for frontend display.

        Returns:
            `str`:
                The generated task id.
        """
        bg_task = BackgroundTask(
            asyncio_task=asyncio_task,
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            tool_name=tool_name,
            summary=summary,
        )
        task_id = bg_task.id
        self.tasks[task_id] = bg_task

        # Install cleanup even if a registry write fails or is cancelled.
        # The callback is attached after writes settle, so a fast task cannot
        # publish a terminal state before its initial running state.
        try:
            await self._message_bus.registry_set(
                MessageBusKeys.bg_tasks(session_id),
                task_id,
                json.dumps(
                    {
                        "tool_name": tool_name,
                        "agent_id": agent_id,
                        "started_at": bg_task.started_at,
                    },
                    ensure_ascii=False,
                ),
                ttl_secs=MessageBusKeys.BG_TASKS_TTL_SECS,
            )
            await self._store_record(
                BackgroundTaskRecord.model_validate(bg_task),
            )
            if (
                self._renewal_task is None
                or self._renewal_task.done()
                or self._renewal_task.cancelling()
            ):
                self._renewal_task = asyncio.create_task(
                    self._renew_running_records(),
                )
        except BaseException:
            asyncio_task.cancel()
            raise
        finally:
            asyncio_task.add_done_callback(
                lambda task: self._on_done(bg_task, task),
            )

        logger.info(
            "Background task registered: task_id=%s, session_id=%s, "
            "agent_id=%s, tool_name=%s",
            task_id,
            session_id,
            agent_id,
            tool_name,
        )
        return task_id

    def _on_done(
        self,
        bg_task: BackgroundTask,
        task: asyncio.Task,
    ) -> None:
        """Record the actual outcome and schedule shared-state cleanup."""
        bg_task.completed_at = time.time()
        if task.cancelled():
            bg_task.status = TaskStatus.CANCELLED
        elif task.exception() is not None:
            bg_task.status = TaskStatus.FAILED
            bg_task.error_summary = str(task.exception())[:512]
        else:
            result = task.result()
            bg_task.status = TaskStatus.COMPLETED
            if isinstance(result, ToolResponse):
                if result.state == ToolResultState.ERROR:
                    bg_task.status = TaskStatus.FAILED
                    bg_task.error_summary = "\n".join(
                        block.text
                        for block in result.content
                        if isinstance(block, TextBlock)
                    )[:512]
                elif result.state == ToolResultState.INTERRUPTED:
                    bg_task.status = TaskStatus.CANCELLED

        self.tasks.pop(bg_task.id, None)
        if not self.tasks and self._renewal_task is not None:
            self._renewal_task.cancel()
        if self._closed:
            return
        completion = asyncio.create_task(
            self._finish_task(BackgroundTaskRecord.model_validate(bg_task)),
        )
        self._completion_tasks.add(completion)
        completion.add_done_callback(self._completion_tasks.discard)

    async def _store_record(self, record: BackgroundTaskRecord) -> None:
        """Publish a snapshot with an expiry independent of the user hash."""
        payload = record.model_dump(mode="json")
        if record.status == TaskStatus.RUNNING:
            payload["_expires_at"] = time.time() + _RECORD_LEASE_SECS
        else:
            assert record.completed_at is not None
            payload["_expires_at"] = (
                record.completed_at + MessageBusKeys.BG_TASKS_TTL_SECS
            )
        await self._message_bus.registry_set(
            MessageBusKeys.bg_task_records(record.user_id),
            record.id,
            json.dumps(payload, ensure_ascii=False),
            ttl_secs=MessageBusKeys.BG_TASKS_TTL_SECS,
        )

    async def _renew_running_records(self) -> None:
        """Renew only locally running tasks without overwriting outcomes."""
        while self.tasks and not self._closed:
            # Renew early enough to allow retries before expiry.
            await asyncio.sleep(_RECORD_LEASE_SECS / 4)
            for task in list(self.tasks.values()):
                if task.asyncio_task.done():
                    continue
                try:
                    async with asyncio.timeout(_IO_TIMEOUT_SECS):
                        namespace = MessageBusKeys.bg_task_records(
                            task.user_id,
                        )
                        value = await self._message_bus.registry_get(
                            namespace,
                            task.id,
                        )
                        if value is None:
                            continue
                        payload = json.loads(value)
                        if payload["status"] != TaskStatus.RUNNING:
                            continue
                        payload["_expires_at"] = (
                            time.time() + _RECORD_LEASE_SECS
                        )
                        # A concurrent completion must win over this lease.
                        await self._message_bus.registry_set_if(
                            namespace,
                            task.id,
                            json.dumps(payload, ensure_ascii=False),
                            expected=value,
                            ttl_secs=MessageBusKeys.BG_TASKS_TTL_SECS,
                        )
                except Exception:
                    logger.exception(
                        "Failed to renew background task lease: task_id=%s",
                        task.id,
                    )

    async def _finish_task(self, record: BackgroundTaskRecord) -> None:
        """Retry pending outcomes and cleanup until success or shutdown."""
        stored = False
        unregistered = False
        while not self._closed:
            try:
                # Outcome persistence never waits for the retention lock.
                async with asyncio.timeout(_IO_TIMEOUT_SECS):
                    if not stored:
                        await self._store_record(record)
                        stored = True
                    if not unregistered:
                        await self._message_bus.registry_del(
                            MessageBusKeys.bg_tasks(record.session_id),
                            record.id,
                        )
                        unregistered = True
                async with asyncio.timeout(_IO_TIMEOUT_SECS):
                    async with self._message_bus.acquire_lock(
                        MessageBusKeys.bg_task_records_lock(record.user_id),
                        ttl_secs=30,
                    ):
                        await self._evict_completed(record.user_id)
                return
            except Exception:
                logger.warning(
                    "Retrying background task finalization: task_id=%s",
                    record.id,
                    exc_info=True,
                )
            await asyncio.sleep(_RETRY_DELAY_SECS)

    async def _evict_completed(self, user_id: str) -> None:
        """Bound terminal history, including records past their read expiry."""
        values = await self._message_bus.registry_getall(
            MessageBusKeys.bg_task_records(user_id),
        )
        completed = []
        for value in values.values():
            record = BackgroundTaskRecord.model_validate_json(value)
            if record.status != TaskStatus.RUNNING:
                completed.append(record)
        completed.sort(key=lambda record: (record.completed_at, record.id))
        for record in completed[:-_MAX_RETAINED_TASKS]:
            await self._message_bus.registry_del(
                MessageBusKeys.bg_task_records(user_id),
                record.id,
            )

    @staticmethod
    def _read_record(value: str) -> BackgroundTaskRecord | None:
        """Hide expired snapshots even if unrelated tasks renew the hash."""
        payload = json.loads(value)
        # Older snapshots have no per-record lease. Bound their lifetime too.
        expires_at = payload.get(
            "_expires_at",
            payload["started_at"] + MessageBusKeys.BG_TASKS_TTL_SECS,
        )
        if expires_at <= time.time():
            return None
        return BackgroundTaskRecord.model_validate(payload)

    # ------------------------------------------------------------------
    # Task querying (for REST endpoints)
    # ------------------------------------------------------------------

    async def list_tasks(
        self,
        *,
        user_id: str,
        session_id: str | None = None,
    ) -> list[BackgroundTaskRecord]:
        """List shared running and retained terminal tasks for a user.

        Args:
            user_id (`str`):
                Only return tasks belonging to this user.
            session_id (`str | None`, optional):
                If provided, further filter by session.

        Returns:
            `list[BackgroundTaskRecord]`:
                Matching tasks ordered by registration time.
        """
        values = await self._message_bus.registry_getall(
            MessageBusKeys.bg_task_records(user_id),
        )
        records = [
            record
            for value in values.values()
            if (record := self._read_record(value)) is not None
        ]
        return sorted(
            (
                record
                for record in records
                if record.user_id == user_id
                and (session_id is None or record.session_id == session_id)
            ),
            key=lambda record: (record.started_at, record.id),
        )

    async def get_task(
        self,
        task_id: str,
        *,
        user_id: str,
    ) -> BackgroundTaskRecord | None:
        """Get shared task metadata, scoped to the requesting user.

        Args:
            task_id (`str`):
                The task identifier.
            user_id (`str`):
                The requesting user's id.

        Returns:
            `BackgroundTaskRecord | None`:
                The task if owned by the user, else `None`.
        """
        value = await self._message_bus.registry_get(
            MessageBusKeys.bg_task_records(user_id),
            task_id,
        )
        if value is None:
            return None
        record = self._read_record(value)
        return (
            record
            if record is not None and record.user_id == user_id
            else None
        )

    # ------------------------------------------------------------------
    # Tool listing
    # ------------------------------------------------------------------

    async def list_tools(self, session_id: str) -> list[ToolBase]:
        """List the background task tools for a given session.

        Args:
            session_id (`str`):
                The current session id (for ToolStop's registry
                lookups).

        Returns:
            `list[ToolBase]`:
                A list containing the :class:`ToolStop` tool.
        """
        return [ToolStop(self.tasks, self._message_bus, session_id)]

    # ------------------------------------------------------------------
    # Session-scoped cancel
    # ------------------------------------------------------------------

    def cancel_session_tasks(self, session_id: str) -> int:
        """Cancel every locally-tracked task whose owner session matches.

        Called by :class:`CancelDispatcher` on each incoming session
        cancel broadcast. Returns the number of tasks cancelled on this
        process.

        Args:
            session_id (`str`):
                The session whose tasks should be cancelled.

        Returns:
            `int`:
                Number of tasks cancelled locally.
        """
        cancelled = 0
        for bg_task in list(self.tasks.values()):
            if bg_task.session_id != session_id or bg_task.asyncio_task.done():
                continue
            logger.info(
                "Cancelling background task for session cancel: "
                "task_id=%s, session_id=%s, agent_id=%s",
                bg_task.id,
                bg_task.session_id,
                bg_task.agent_id,
            )
            bg_task.asyncio_task.cancel()
            cancelled += 1
        return cancelled

    # ------------------------------------------------------------------
    # Single-task cancel (called by CancelDispatcher on bus signal)
    # ------------------------------------------------------------------

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a single locally-tracked task by its id.

        Called by :class:`CancelDispatcher` when a task-level cancel
        broadcast arrives. Returns whether the task was found and
        cancelled on this process.

        Args:
            task_id (`str`):
                The task to cancel.

        Returns:
            `bool`:
                ``True`` if the task was found locally and cancelled.
        """
        bg_task = self.tasks.get(task_id)
        if bg_task is None or bg_task.asyncio_task.done():
            return False
        logger.info(
            "Cancelling background task via bus signal: "
            "task_id=%s, session_id=%s, agent_id=%s",
            task_id,
            bg_task.session_id,
            bg_task.agent_id,
        )
        bg_task.asyncio_task.cancel()
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Enter the async context. No setup required.

        Returns:
            `Self`: This manager instance.
        """
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Cancel work and flush outcomes within a bounded shutdown budget."""
        count = len(self.tasks)
        logger.info(
            "Shutting down BackgroundTaskManager: cancelling %d task(s).",
            count,
        )
        tasks = [bg.asyncio_task for bg in self.tasks.values()]
        if self._renewal_task is not None:
            self._renewal_task.cancel()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _SHUTDOWN_TIMEOUT_SECS
        # Reserve the last 10% of the budget for cancellation cleanup.
        for bg_task in list(self.tasks.values()):
            logger.info(
                "Cancelling background task on shutdown: task_id=%s, "
                "session_id=%s, agent_id=%s",
                bg_task.id,
                bg_task.session_id,
                bg_task.agent_id,
            )
            bg_task.asyncio_task.cancel()
        try:
            if tasks:
                await asyncio.wait(
                    tasks,
                    timeout=max(
                        0,
                        deadline - loop.time() - _SHUTDOWN_TIMEOUT_SECS / 10,
                    ),
                )
            if self._completion_tasks:
                await asyncio.wait(
                    self._completion_tasks,
                    timeout=max(
                        0,
                        deadline - loop.time() - _SHUTDOWN_TIMEOUT_SECS / 10,
                    ),
                )
        finally:
            # Late execution callbacks must not start I/O after the bus closes.
            self._closed = True
            pending = {
                task
                for task in [
                    *tasks,
                    *self._completion_tasks,
                    self._renewal_task,
                ]
                if task is not None and not task.done()
            }
            for task in pending:
                task.cancel()
            if pending:
                _, unfinished = await asyncio.wait(
                    pending,
                    timeout=max(0, deadline - loop.time()),
                )
                logger.warning(
                    "Background task shutdown budget exhausted: "
                    "cancelled=%d, unfinished=%d",
                    len(pending),
                    len(unfinished),
                )
            self.tasks.clear()
