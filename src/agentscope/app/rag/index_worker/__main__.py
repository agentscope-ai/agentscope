# -*- coding: utf-8 -*-
"""Entry point for ``python -m agentscope.app.rag.index_worker``.

Resolves a deployment-supplied bootstrap callable from the
``AGENTSCOPE_WORKER_BOOTSTRAP`` environment variable, calls it to
obtain the concrete backends, and hands them to :func:`run_worker`.

The deployment owns the bootstrap because backend selection is a
deployment concern — there is no one-size-fits-all storage,
message bus, or blob store. The bootstrap must be importable, must
take no arguments, and must return a dict whose keys match
:func:`run_worker`'s keyword arguments.

Example bootstrap (``mydeploy/worker_bootstrap.py``)::

    import os

    from agentscope.app.rag.blob_store import S3BlobStore
    from agentscope.app.rag.knowledge_base_manager import (
        DefaultKnowledgeBaseManager,
    )
    from agentscope.app.message_bus import RedisMessageBus
    from agentscope.app.storage import RedisStorage
    from agentscope.rag import ApproxTokenChunker, TextParser

    def bootstrap() -> dict:
        return {
            "storage": RedisStorage(url=os.environ["REDIS_URL"]),
            "message_bus": RedisMessageBus(url=os.environ["REDIS_URL"]),
            "blob_store": S3BlobStore(
                bucket=os.environ["S3_BUCKET"],
                endpoint_url=os.environ.get("S3_ENDPOINT"),
            ),
            "knowledge_base_manager": DefaultKnowledgeBaseManager(...),
            "parsers": [TextParser()],
            "chunker": ApproxTokenChunker(),
        }

And launch::

    AGENTSCOPE_WORKER_BOOTSTRAP=mydeploy.worker_bootstrap:bootstrap \\
        python -m agentscope.app.rag.index_worker
"""
import asyncio
import importlib
import logging
import os
import sys
from typing import Any, Callable

from . import run_worker


def _resolve(dotted: str) -> Callable[[], dict[str, Any]]:
    """Import ``module:attribute`` and return the attribute.

    Args:
        dotted (`str`):
            A ``module:attribute`` reference; must contain a colon.

    Returns:
        `Callable[[], dict[str, Any]]`:
            The resolved attribute — expected to be a zero-arg
            callable that returns the kwargs dict for
            :func:`run_worker`.

    Raises:
        `ValueError`:
            When ``dotted`` does not contain a colon.
    """
    if ":" not in dotted:
        raise ValueError(
            f"AGENTSCOPE_WORKER_BOOTSTRAP must be in 'module:attr' "
            f"form, got {dotted!r}.",
        )
    module_name, _, attr = dotted.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def main() -> None:
    """Resolve the bootstrap callable from the environment and run the worker.

    Reads ``AGENTSCOPE_WORKER_BOOTSTRAP`` (``module:attr`` form),
    imports the target, calls it for the kwargs dict, and forwards
    them to :func:`run_worker`.  Exits with code ``2`` when the
    environment variable is missing — the deployment must supply it
    because backend selection is a deployment concern.
    """
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    bootstrap_path = os.environ.get("AGENTSCOPE_WORKER_BOOTSTRAP")
    if not bootstrap_path:
        sys.stderr.write(
            "AGENTSCOPE_WORKER_BOOTSTRAP is required — set it to "
            "'package.module:callable' that returns a kwargs dict for "
            "run_worker.\n",
        )
        sys.exit(2)
    factory = _resolve(bootstrap_path)
    kwargs = factory()
    asyncio.run(run_worker(**kwargs))


if __name__ == "__main__":
    main()
