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
    IndexTaskConsumer,
    IndexWorker,
    KnowledgeBaseService,
    SessionService,
)

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
    knowledge_base_manager = app.state.knowledge_base_manager
    blob_store = app.state.blob_store
    enable_index_worker = app.state.enable_index_worker

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(storage)
        await stack.enter_async_context(message_bus)
        await stack.enter_async_context(workspace_manager)
        if knowledge_base_manager is not None:
            # ``KnowledgeBaseManagerBase.__aenter__`` enters the bound
            # vector store too, so a single context covers both.
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
            knowledge_base_manager=knowledge_base_manager,
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
        knowledge_base_service = None
        if knowledge_base_manager is not None:
            # Indexing is uniformly driven by the message bus: the
            # service publishes an index-task entry, and a consumer
            # (in-process or in a dedicated worker process) drains it.
            #
            # * Embedded — ``enable_index_worker=True``: this lifespan
            #   additionally starts an :class:`IndexWorker` plus an
            #   :class:`IndexTaskConsumer` that subscribes to the same
            #   channel. The ``InMemoryMessageBus`` makes the round
            #   trip near-free; everything runs in one binary.
            #
            # * Dedicated — ``enable_index_worker=False``: this
            #   lifespan does NOT start a worker. One or more separate
            #   processes (``python -m agentscope.app.rag.index_worker``)
            #   run their own consumer + worker pair subscribed to the
            #   same channel.
            #
            # The sweeper STILL runs in the API process either way,
            # because the API is the only resource guaranteed to be
            # live whenever uploads happen — if the publish ever races
            # a worker restart the durable queue catches the task, and
            # if the queue write itself failed the sweeper eventually
            # re-enqueues from storage.
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
                await stack.enter_async_context(
                    IndexTaskConsumer(
                        message_bus=message_bus,
                        worker=worker,
                    ),
                )

            sweeper = IndexSweeper(
                storage=storage,
                message_bus=message_bus,
            )
            await sweeper.start()
            stack.push_async_callback(sweeper.stop)

            knowledge_base_service = KnowledgeBaseService(
                storage=storage,
                knowledge_base_manager=knowledge_base_manager,
                blob_store=blob_store,
                message_bus=message_bus,
            )

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
