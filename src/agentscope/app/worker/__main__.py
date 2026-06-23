# -*- coding: utf-8 -*-
"""Entry point for ``python -m agentscope.app.worker``.

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

    from agentscope.app.blob_store import S3BlobStore
    from agentscope.app.knowledge_base_manager import (
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
        python -m agentscope.app.worker
"""
import asyncio
import importlib
import logging
import os
import sys

from . import run_worker


def _resolve(dotted: str):
    """Import ``module:attribute`` and return the attribute."""
    if ":" not in dotted:
        raise ValueError(
            f"AGENTSCOPE_WORKER_BOOTSTRAP must be in 'module:attr' "
            f"form, got {dotted!r}.",
        )
    module_name, _, attr = dotted.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def main() -> None:
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
