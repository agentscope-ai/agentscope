# -*- coding: utf-8 -*-
"""The tracing types class in agentscope."""
from enum import Enum

from opentelemetry import trace

StatusCode = trace.StatusCode


class SpanKind(str, Enum):
    """The span kind."""

    AGENT = "AGENT"
    TOOL = "TOOL"
    LLM = "LLM"
    EMBEDDING = "EMBEDDING"
    FORMATTER = "FORMATTER"
    COMMON = "COMMON"


class SpanAttributes:
    """The span attributes."""

    SPAN_KIND = "span.kind"
    OUTPUT = "output"
    INPUT = "input"
    META = "metadata"
    PROJECT_RUN_ID = "project.run_id"
