# -*- coding: utf-8 -*-
"""Reliable cross-process dispatcher for HITL/external resume commands."""

import asyncio
import os
import socket
import uuid
from typing import TYPE_CHECKING, Self

from pydantic import TypeAdapter

from ..._logging import logger
from ...event import (
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message_bus import MessageBusKeys

if TYPE_CHECKING:
    from .._service import ChatService
    from ..message_bus import MessageBus
    from ..storage import StorageBase
    from ._chat_run_registry import ChatRunRegistry


_RESUME_INPUT_ADAPTER: TypeAdapter = TypeAdapter(
    UserConfirmResultEvent | ExternalExecutionResultEvent | UserInterruptEvent,
)


class ResumeDispatcher:
    """Consume the dedicated resume stream with claim/heartbeat/ACK.

    A stream entry remains in Redis's pending-entry list until the chat
    service confirms that the updated session state and resume idempotency
    marker were persisted.  Crashed work is reclaimed by another API process;
    healthy long-running work refreshes its claim so it is not stolen.
    """

    def __init__(
        self,
        message_bus: "MessageBus",
        storage: "StorageBase",
        chat_service: "ChatService",
        chat_run_registry: "ChatRunRegistry",
        *,
        consumer_name: str | None = None,
        claim_idle_ms: int = MessageBusKeys.RESUME_CLAIM_IDLE_MS,
        read_block_ms: int = MessageBusKeys.RESUME_READ_BLOCK_MS,
        reclaim_interval_ms: int = (MessageBusKeys.RESUME_RECLAIM_INTERVAL_MS),
        max_concurrency: int = MessageBusKeys.RESUME_MAX_CONCURRENCY,
    ) -> None:
        self._bus = message_bus
        self._storage = storage
        self._chat_service = chat_service
        self._registry = chat_run_registry
        self._consumer = consumer_name or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self._claim_idle_ms = claim_idle_ms
        self._read_block_ms = max(1, read_block_ms)
        self._reclaim_interval_ms = max(1, reclaim_interval_ms)
        self._max_concurrency = max(1, max_concurrency)
        self._task: asyncio.Task[None] | None = None
        self._entry_tasks: set[asyncio.Task[None]] = set()

    async def __aenter__(self) -> Self:
        await self._bus.reliable_queue_ensure_group(
            MessageBusKeys.resume_queue(),
            MessageBusKeys.RESUME_CONSUMER_GROUP,
        )
        self._task = asyncio.create_task(
            self._loop(),
            name=f"resume-dispatcher:{self._consumer}",
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

        entry_tasks = list(self._entry_tasks)
        for task in entry_tasks:
            task.cancel()
        if entry_tasks:
            await asyncio.gather(*entry_tasks, return_exceptions=True)
        self._entry_tasks.clear()

    def _entry_done(self, task: asyncio.Task[None]) -> None:
        """Remove a completed entry task and retrieve unexpected errors."""
        self._entry_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "ResumeDispatcher entry task failed unexpectedly.",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _wait_for_capacity(self) -> None:
        """Apply per-process backpressure before claiming more entries."""
        if len(self._entry_tasks) < self._max_concurrency:
            return
        await asyncio.wait(
            self._entry_tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

    async def _loop(self) -> None:
        key = MessageBusKeys.resume_queue()
        group = MessageBusKeys.RESUME_CONSUMER_GROUP
        event_loop = asyncio.get_running_loop()
        next_reclaim_at = 0.0
        while True:
            try:
                await self._wait_for_capacity()
                now = event_loop.time()
                entries: list[tuple[str, dict]] = []
                if now >= next_reclaim_at:
                    entries = await self._bus.reliable_queue_reclaim(
                        key,
                        group,
                        self._consumer,
                        min_idle_ms=self._claim_idle_ms,
                        max_count=1,
                    )
                    next_reclaim_at = now + self._reclaim_interval_ms / 1000
                    if entries:
                        logger.info(
                            "ResumeDispatcher consumer %s reclaimed %d "
                            "abandoned entry.",
                            self._consumer,
                            len(entries),
                        )
                if not entries:
                    until_reclaim_ms = max(
                        1,
                        int((next_reclaim_at - event_loop.time()) * 1000),
                    )
                    entries = await self._bus.reliable_queue_read(
                        key,
                        group,
                        self._consumer,
                        max_count=1,
                        block_ms=min(
                            self._read_block_ms,
                            until_reclaim_ms,
                        ),
                    )
                for entry_id, payload in entries:
                    task = asyncio.create_task(
                        self._process_entry(entry_id, payload),
                        name=f"resume-entry:{entry_id}",
                    )
                    self._entry_tasks.add(task)
                    task.add_done_callback(self._entry_done)
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "ResumeDispatcher consumer %s loop failed; retrying.",
                    self._consumer,
                )
                await asyncio.sleep(0.1)

    async def _process_entry(self, entry_id: str, payload: dict) -> None:
        key = MessageBusKeys.resume_queue()
        group = MessageBusKeys.RESUME_CONSUMER_GROUP

        try:
            user_id = payload["user_id"]
            session_id = payload["session_id"]
            agent_id = payload["agent_id"]
            raw_input = payload["input"]
            input_msg = _RESUME_INPUT_ADAPTER.validate_python(raw_input)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "ResumeDispatcher: terminal malformed entry %s: %r",
                entry_id,
                payload,
            )
            await self._bus.reliable_queue_ack(key, group, [entry_id])
            return

        heartbeat = asyncio.create_task(
            self._heartbeat(entry_id),
            name=f"resume-heartbeat:{entry_id}",
        )
        try:
            if (
                await self._storage.get_session(
                    user_id,
                    agent_id,
                    session_id,
                )
                is None
            ):
                logger.warning(
                    "ResumeDispatcher: ACKing entry %s for deleted session "
                    "%s.",
                    entry_id,
                    session_id,
                )
                await self._bus.reliable_queue_ack(key, group, [entry_id])
                return

            # A local HTTP/wake run can occupy the per-process registry even
            # before its distributed lock is visible. Keep the same pending
            # stream entry and wait; never manufacture a retry entry.
            while True:
                run_coro = self._chat_service.run_reliable_resume(
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    input_msg=input_msg,
                )
                try:
                    run_task = self._registry.spawn(
                        run_coro,
                        session_id=session_id,
                        name=f"resume-run:{session_id}:{input_msg.id}",
                    )
                    break
                except RuntimeError:
                    run_coro.close()
                    await asyncio.sleep(0.1)

            durable = await run_task
            if durable:
                await self._bus.reliable_queue_ack(key, group, [entry_id])
                logger.info(
                    "ResumeDispatcher: ACKed entry %s event %s session %s.",
                    entry_id,
                    input_msg.id,
                    session_id,
                )
            else:
                logger.warning(
                    "ResumeDispatcher: leaving entry %s pending after a "
                    "non-durable setup failure.",
                    entry_id,
                )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "ResumeDispatcher: entry %s failed and remains pending.",
                entry_id,
            )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _heartbeat(self, entry_id: str) -> None:
        interval = max(0.01, self._claim_idle_ms / 3000)
        while True:
            await asyncio.sleep(interval)
            try:
                await self._bus.reliable_queue_touch(
                    MessageBusKeys.resume_queue(),
                    MessageBusKeys.RESUME_CONSUMER_GROUP,
                    self._consumer,
                    [entry_id],
                )
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "ResumeDispatcher: failed to heartbeat entry %s.",
                    entry_id,
                )
