# -*- coding: utf-8 -*-
"""Out-of-process index worker entry point.

A worker process owns:

- a :class:`~agentscope.app._service.IndexWorker` instance, which
  runs the parse → chunk → embed pipeline for one document at a time;
- an :class:`~agentscope.app._service.IndexTaskConsumer`, which
  subscribes to the shared task channel on the message bus and
  feeds the worker.

It does NOT own the sweeper — the API process keeps running the
sweeper so that documents stuck in ``pending`` (because the API
crashed between the storage write and the dispatcher publish) still
recover, even if every worker process happens to be offline at the
moment the publish was attempted.

This module is a library: a deployment wires its concrete backends
through :func:`run_worker` from whatever bootstrap script it uses
(systemd unit, Kubernetes Deployment + container entrypoint,
docker-compose ``command``, ...). The shape mirrors
:func:`agentscope.app.create_app` — pass already-constructed
backend instances, the worker manages their lifecycle through an
:class:`AsyncExitStack`.

Example::

    import asyncio
    import socket
    import uuid

    from agentscope.app.rag.blob_store import S3BlobStore
    from agentscope.app.rag.knowledge_base_manager import (
        DefaultKnowledgeBaseManager,
    )
    from agentscope.app.message_bus import RedisMessageBus
    from agentscope.app.storage import RedisStorage
    from agentscope.app.rag.index_worker import run_worker
    from agentscope.rag import TextParser, ...

    async def main() -> None:
        storage = RedisStorage(url=os.environ["REDIS_URL"])
        message_bus = RedisMessageBus(url=os.environ["REDIS_URL"])
        blob_store = S3BlobStore(
            bucket=os.environ["S3_BUCKET"],
            endpoint_url=os.environ.get("S3_ENDPOINT"),
        )
        kb_manager = DefaultKnowledgeBaseManager(...)
        parsers = [TextParser()]
        await run_worker(
            storage=storage,
            message_bus=message_bus,
            blob_store=blob_store,
            knowledge_base_manager=kb_manager,
            parsers=parsers,
        )

    if __name__ == "__main__":
        asyncio.run(main())
"""
import asyncio
import signal
import socket
import uuid
from concurrent.futures import ProcessPoolExecutor
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from ..._service import IndexTaskConsumer, IndexWorker
from ...._logging import logger

if TYPE_CHECKING:
    from ..blob_store import BlobStoreBase
    from ..knowledge_base_manager import KnowledgeBaseManagerBase
    from ...message_bus import MessageBus
    from ...storage import StorageBase
    from ....rag import ParserBase


async def run_worker(
    *,
    storage: "StorageBase",
    message_bus: "MessageBus",
    blob_store: "BlobStoreBase",
    knowledge_base_manager: "KnowledgeBaseManagerBase",
    parsers: "list[ParserBase] | dict[str, ParserBase]",
    node_id: str | None = None,
    worker_max_concurrency: int = 4,
    consumer_max_batch: int = 32,
    parser_executor: ProcessPoolExecutor | None = None,
) -> None:
    """Run the out-of-process index worker until cancelled.

    Manages the lifecycle of every passed-in backend through a single
    :class:`AsyncExitStack`. The function returns when SIGINT or
    SIGTERM is delivered (deployment-controlled shutdown).

    Args:
        storage (`StorageBase`):
            Persistent storage backend. Lifecycle managed here.
        message_bus (`MessageBus`):
            Live message bus subscribed to for index-task signals.
            Lifecycle managed here.
        blob_store (`BlobStoreBase`):
            Backend the worker reads document bytes from. Lifecycle
            managed here.
        knowledge_base_manager (`KnowledgeBaseManagerBase`):
            Resolves :class:`KnowledgeBase` runtimes for embedding +
            vector store writes. Lifecycle managed here.
        parsers (`list[ParserBase] | dict[str, ParserBase]`):
            Parsers used to dispatch uploads by IANA media type.
            Pass the same registry both to ``create_app`` and to the
            worker — list mode expands each parser's
            ``supported_media_types`` (later entries override earlier
            ones, with a warning); dict mode is used verbatim for
            explicit routing.
        node_id (`str | None`, optional):
            Stable identifier for this worker process used on the
            storage lease. Defaults to
            ``"{hostname}:{uuid-prefix}"`` — good enough for
            distinguishing two workers running on the same host;
            override when your orchestrator has a more meaningful
            id (Kubernetes pod name, ECS task id, ...).
        worker_max_concurrency (`int`, defaults to ``4``):
            Maximum number of documents the worker processes
            concurrently. See
            :class:`~agentscope.app._service.IndexWorker`.
        consumer_max_batch (`int`, defaults to ``32``):
            Maximum entries the consumer drains per signal. See
            :class:`~agentscope.app._service.IndexTaskConsumer`.
        parser_executor (`ProcessPoolExecutor | None`, optional):
            Process pool for CPU-bound parses. ``None`` runs parses
            inline (fine for text-only deployments).
    """
    resolved_node_id = (
        node_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
    )

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(storage)
        await stack.enter_async_context(message_bus)
        await stack.enter_async_context(blob_store)
        await stack.enter_async_context(knowledge_base_manager)

        worker = IndexWorker(
            storage=storage,
            blob_store=blob_store,
            knowledge_base_manager=knowledge_base_manager,
            parsers=parsers,
            node_id=resolved_node_id,
            max_concurrency=worker_max_concurrency,
            parser_executor=parser_executor,
        )
        await stack.enter_async_context(
            IndexTaskConsumer(
                message_bus=message_bus,
                worker=worker,
                max_batch=consumer_max_batch,
            ),
        )

        logger.info(
            "Index worker %s ready (max_concurrency=%d, max_batch=%d)",
            resolved_node_id,
            worker_max_concurrency,
            consumer_max_batch,
        )

        # Block until a signal arrives. We install handlers on the
        # running loop rather than using ``signal.signal`` so the
        # interaction with asyncio is well-defined (the default
        # handler would raise KeyboardInterrupt at an arbitrary
        # await point, which is awkward to clean up).
        loop = asyncio.get_running_loop()
        stop = loop.create_future()

        def _request_stop() -> None:
            if not stop.done():
                stop.set_result(None)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except NotImplementedError:
                # Windows event loop does not implement add_signal_handler;
                # tests and Linux/macOS deployments are unaffected.
                pass

        try:
            await stop
        finally:
            logger.info("Index worker %s shutting down", resolved_node_id)
