# -*- coding: utf-8 -*-
"""The agentscope serialization module"""
import os

import requests

from . import exception
from . import module
from . import message
from . import model
from . import tool
from . import formatter
from . import memory
from . import agent
from . import session
from . import embedding
from . import token
from . import evaluate
from . import pipeline
from . import tracing
from . import rag

from ._logging import (
    logger,
    setup_logger,
)
from .hooks import _equip_as_studio_hooks
from ._version import __version__


def init(
    project: str | None = None,
    name: str | None = None,
    logging_path: str | None = None,
    logging_level: str = "INFO",
    studio_url: str | None = None,
    tracing_url: str | None = None,
) -> None:
    """Initialize the agentscope library.

    Args:
        project (`str | None`, optional):
            The project name.
        name (`str | None`, optional):
            The name of the run.
        logging_path (`str | None`, optional):
            The path to saving the log file. If not provided, logs will not be
            saved.
        logging_level (`str | None`, optional):
            The logging level. Defaults to "INFO".
        studio_url (`str | None`, optional):
            The URL of the AgentScope Studio to connect to.
        tracing_url (`str | None`, optional):
            The URL of the tracing endpoint, which can connect to third-party
            OpenTelemetry tracing platforms like Arize-Phoenix and Langfuse.
            If not provided and `studio_url` is provided, it will send traces
            to the AgentScope Studio's tracing endpoint.
    """

    from . import _config

    if project:
        _config.project = project

    if name:
        _config.name = name

    setup_logger(logging_level, logging_path)

    if studio_url:
        # Register the run
        data = {
            "id": _config.run_id,
            "project": _config.project,
            "name": _config.name,
            "timestamp": _config.created_at,
            "pid": os.getpid(),
            "status": "running",
            # Deprecated fields
            "run_dir": "",
        }
        response = requests.post(
            url=f"{studio_url}/trpc/registerRun",
            json=data,
        )
        response.raise_for_status()

        from .agent import UserAgent, StudioUserInput

        UserAgent.override_class_input_method(
            StudioUserInput(
                studio_url=studio_url,
                run_id=_config.run_id,
                max_retries=3,
            ),
        )

        _equip_as_studio_hooks(studio_url)

    if tracing_url:
        endpoint = tracing_url
    else:
        endpoint = studio_url.strip("/") + "/v1/traces" if studio_url else None

    if endpoint:
        from .tracing import setup_tracing

        setup_tracing(endpoint=endpoint)


__all__ = [
    # modules
    "exception",
    "module",
    "message",
    "model",
    "tool",
    "formatter",
    "memory",
    "agent",
    "session",
    "logger",
    "embedding",
    "token",
    "evaluate",
    "pipeline",
    "tracing",
    "rag",
    # functions
    "init",
    "setup_logger",
    "__version__",
]
