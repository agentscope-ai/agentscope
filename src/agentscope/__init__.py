# -*- coding: utf-8 -*-
# flake8: noqa: E402
# pylint: disable=wrong-import-position, unused-argument
"""The agentscope serialization module"""
import os
import sys
import warnings
import importlib
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec
from contextvars import ContextVar
from datetime import datetime
from types import ModuleType
from typing import Optional, Any, Sequence

import requests
import shortuuid

from ._run_config import _ConfigCls
from ._extensions_map import _get_extension_map


def _generate_random_suffix(length: int) -> str:
    """Generate a random suffix."""
    return shortuuid.uuid()[:length]


# A thread and async safe global configuration instance
_config = _ConfigCls(
    run_id=ContextVar("run_id", default=shortuuid.uuid()),
    project=ContextVar(
        "project",
        default="UnnamedProject_At" + datetime.now().strftime("%Y%m%d"),
    ),
    name=ContextVar(
        "name",
        default=datetime.now().strftime("%H%M%S_")
        + _generate_random_suffix(4),
    ),
    created_at=ContextVar(
        "created_at",
        default=datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
    ),
    trace_enabled=ContextVar(
        "trace_enabled",
        default=False,
    ),
)


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
from . import a2a

from ._logging import (
    logger,
    setup_logger,
)
from .hooks import _equip_as_studio_hooks
from ._version import __version__

# Raise each warning only once
warnings.filterwarnings("once", category=DeprecationWarning)


def init(
    project: str | None = None,
    name: str | None = None,
    run_id: str | None = None,
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
        run_id (`str | None`, optional):
            The identity of a running instance, which can be an agent, or a
            multi-agent system. The `run_id` is used in AgentScope-Studio to
            distinguish different runs.
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

    if project:
        _config.project = project

    if name:
        _config.name = name

    if run_id:
        _config.run_id = run_id

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
        _config.trace_enabled = True


_extension_map = _get_extension_map()


def _lazy_import_ext(import_name: str, pip_name: str, name: str) -> Any:
    try:
        _module = importlib.import_module(import_name)
        sys.modules[f"{__name__}.{name}"] = _module
        return _module
    except ImportError as e:
        raise ImportError(
            f"Missing extension! To use 'agentscope.{name}', please install '{pip_name}'.",
        ) from e


def __getattr__(name: str) -> Any:
    if name in _extension_map:
        mapping = _extension_map[name]
        import_name = mapping["import_name"]
        pip_name = mapping["pip_name"]
        return _lazy_import_ext(import_name, pip_name, name)
    raise AttributeError(f"module {__name__} has no attribute {name}")


class _ExtensionFinder(MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Optional[Sequence[str]],
        target: Optional[ModuleType] = None,
    ) -> Optional[ModuleSpec]:
        """
        Locate and return the import specification for an agentscope extension.

        This MetaPathFinder intercepts imports of the form `agentscope.<ext>`
        and maps them to the corresponding real extension package defined in
        `_extension_map`. For example:

            agentscope.runtime.sandbox -> agentscope_runtime.sandbox

        Args:
            fullname: Fully-qualified module name being imported.
            path: Package search path for submodules (or None for top-level).
            target: Target module object if this is a reload, else None.

        Returns:
            The ModuleSpec for the located module, or None if not handled.
        """
        if fullname.startswith("agentscope."):
            parts = fullname.split(".")
            if len(parts) >= 2:
                ext_name = parts[1]
                if ext_name in _extension_map:
                    real_pkg = _extension_map[ext_name]["import_name"]
                    real_fullname = ".".join(
                        [real_pkg] + parts[2:],
                    )
                    _module = importlib.import_module(real_fullname)
                    sys.modules[fullname] = _module
                    return _module.__spec__
        return None


sys.meta_path.insert(0, _ExtensionFinder())

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
    "a2a",
    # functions
    "init",
    "setup_logger",
    "__version__",
]
