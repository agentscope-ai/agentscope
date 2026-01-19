# -*- coding: utf-8 -*-
"""Tracing public API.

We keep imports lazy to avoid tracing↔model circular imports, while still
exporting concrete names so linters can resolve them.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from ._setup import setup_tracing

R = TypeVar("R")


def trace(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    from ._trace import trace as _trace

    return _trace(*args, **kwargs)


def trace_llm(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    from ._trace import trace_llm as _trace_llm

    return _trace_llm(*args, **kwargs)


def trace_reply(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    from ._trace import trace_reply as _trace_reply

    return _trace_reply(*args, **kwargs)


def trace_format(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    from ._trace import trace_format as _trace_format

    return _trace_format(*args, **kwargs)


def trace_toolkit(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    from ._trace import trace_toolkit as _trace_toolkit

    return _trace_toolkit(*args, **kwargs)


def trace_embedding(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    from ._trace import trace_embedding as _trace_embedding

    return _trace_embedding(*args, **kwargs)


__all__ = [
    "setup_tracing",
    "trace",
    "trace_llm",
    "trace_reply",
    "trace_format",
    "trace_toolkit",
    "trace_embedding",
]
