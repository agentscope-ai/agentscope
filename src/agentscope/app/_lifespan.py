# -*- coding: utf-8 -*-
"""The lifespan of the agent service."""
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from agentscope.app._manager import (
    BackgroundTaskManager,
    SchedulerManager,
    WakeupDispatcher,
)
from ._service import ChatService

if TYPE_CHECKING:
    from fastapi import FastAPI
else:
    FastAPI = Any


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown of all application-wide resources.

    Every resource with a lifecycle is an async context manager and is
    entered through a single :class:`AsyncExitStack`. The stack tears
    them down in reverse order on shutdown — including when an entry
    later in the sequence raises during startup, so no resource leaks
    on partial failure.

    Service-layer ``ChatService`` has no lifecycle of its own and is
    constructed inline.
    """
    storage = app.state.storage
    message_bus = app.state.message_bus
    workspace_manager = app.state.workspace_manager

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(storage)
        await stack.enter_async_context(message_bus)
        await stack.enter_async_context(workspace_manager)

        bg_manager = await stack.enter_async_context(BackgroundTaskManager())

        # Scheduler is independent of ChatService now (its fire path
        # pushes to inbox + enqueues wakeup via the bus), so we build it
        # before ChatService and inject it via the constructor.
        scheduler = await stack.enter_async_context(
            SchedulerManager(
                storage=storage,
                message_bus=message_bus,
            ),
        )
        app.state.scheduler_manager = scheduler

        chat_service = ChatService(
            storage=storage,
            workspace_manager=workspace_manager,
            scheduler_manager=scheduler,
            background_task_manager=bg_manager,
            message_bus=message_bus,
            extra_agent_middlewares=app.state.extra_agent_middlewares,
            extra_agent_tools=app.state.extra_agent_tools,
        )
        app.state.chat_service = chat_service

        # WakeupDispatcher needs a live reference somewhere, or it would
        # be garbage-collected; the AsyncExitStack holds that reference
        # for us, so we don't need a local binding or app.state slot.
        await stack.enter_async_context(
            WakeupDispatcher(
                message_bus=message_bus,
                chat_service=chat_service,
            ),
        )

        yield
