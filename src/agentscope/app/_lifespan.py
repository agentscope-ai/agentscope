# -*- coding: utf-8 -*-
"""The lifespan of the agent service."""
import socket
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from ._manager import (
    BackgroundTaskManager,
    CancelDispatcher,
    ChatRunRegistry,
    SchedulerManager,
    WakeupDispatcher,
)
from ._service import (
    ChatService,
    IndexSweeper,
    IndexWorker,
    KnowledgeBaseService,
    SessionService,
)
from .index_dispatch import InProcessDispatcher, MessageBusDispatcher

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

    Service-layer ``ChatService`` and ``SessionService`` have no
    lifecycle of their own and are constructed inline.
    """
    storage = app.state.storage
    message_bus = app.state.message_bus
    workspace_manager = app.state.workspace_manager
    vector_store = app.state.vector_store
    knowledge_base_manager = app.state.knowledge_base_manager
    blob_store = app.state.blob_store
    enable_index_worker = app.state.enable_index_worker

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(storage)
        await stack.enter_async_context(message_bus)
        await stack.enter_async_context(workspace_manager)
        if vector_store is not None:
            await stack.enter_async_context(vector_store)
        if knowledge_base_manager is not None:
            await stack.enter_async_context(knowledge_base_manager)
        if blob_store is not None:
            await stack.enter_async_context(blob_store)

        bg_manager = await stack.enter_async_context(
            BackgroundTaskManager(message_bus=message_bus),
        )
        app.state.background_task_manager = bg_manager

        # Per-process registry of in-flight chat-run asyncio tasks.
        # Entered before the wake-up + cancel dispatchers so they can
        # share the same registry; exited last so its shutdown can
        # cancel any leftover runs after the dispatchers stop.
        chat_run_registry = await stack.enter_async_context(ChatRunRegistry())
        app.state.chat_run_registry = chat_run_registry

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
            custom_subagent_templates=app.state.custom_subagent_templates,
            custom_agent_cls=app.state.custom_agent_cls,
        )
        app.state.chat_service = chat_service

        app.state.session_service = SessionService(
            storage=storage,
            message_bus=message_bus,
        )

        # ---------------- Knowledge-base wiring ----------------
        index_dispatcher = None
        knowledge_base_service = None
        if knowledge_base_manager is not None:
            # Two deployment modes share this branch; both produce an
            # ``IndexDispatcherBase`` plus an ``IndexSweeper`` running
            # against the same storage so crashed handoffs recover.
            #
            # * Embedded — ``enable_index_worker=True``: the API process
            #   owns the :class:`IndexWorker` and the dispatcher hands
            #   off in-process. Simple deployment; everything in one
            #   binary.
            #
            # * Dedicated — ``enable_index_worker=False``: the API
            #   process does NOT own a worker. Dispatch becomes a
            #   message-bus publish that one or more separate worker
            #   processes (running ``python -m agentscope.app.worker``)
            #   pick up. The sweeper STILL runs in the API process
            #   because the API is the only resource guaranteed to be
            #   live whenever uploads happen — if the publish ever
            #   races a worker restart the durable queue catches the
            #   task, and if the queue write itself failed the sweeper
            #   eventually re-dispatches from storage.
            if enable_index_worker:
                node_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
                worker = IndexWorker(
                    storage=storage,
                    blob_store=blob_store,
                    knowledge_base_manager=knowledge_base_manager,
                    parsers=app.state.knowledge_parsers,
                    chunker=app.state.knowledge_chunker,
                    node_id=node_id,
                )
                index_dispatcher = await stack.enter_async_context(
                    InProcessDispatcher(worker),
                )
            else:
                index_dispatcher = await stack.enter_async_context(
                    MessageBusDispatcher(message_bus),
                )

            sweeper = IndexSweeper(
                storage=storage,
                dispatcher=index_dispatcher,
            )
            await sweeper.start()
            stack.push_async_callback(sweeper.stop)

            knowledge_base_service = KnowledgeBaseService(
                storage=storage,
                knowledge_base_manager=knowledge_base_manager,
                blob_store=blob_store,
                dispatcher=index_dispatcher,
            )

        app.state.index_dispatcher = index_dispatcher
        app.state.knowledge_base_service = knowledge_base_service

        # Dispatchers need live references somewhere, or they would be
        # garbage-collected; the AsyncExitStack holds those references
        # for us, so we don't need local bindings or app.state slots.
        await stack.enter_async_context(
            WakeupDispatcher(
                message_bus=message_bus,
                storage=storage,
                chat_service=chat_service,
                chat_run_registry=chat_run_registry,
            ),
        )
        await stack.enter_async_context(
            CancelDispatcher(
                message_bus=message_bus,
                registry=chat_run_registry,
                bg_manager=bg_manager,
            ),
        )

        yield
