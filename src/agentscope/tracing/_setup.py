# -*- coding: utf-8 -*-
"""The tracing interface class in agentscope."""
from opentelemetry import trace as trace_api
from agentscope import _config


def setup_tracing(endpoint: str) -> None:
    """Set up the AgentScope tracing by configuring the endpoint URL.

    Args:
        endpoint (`str`):
            The endpoint URL for the tracing exporter.
    """
    # Lazy import
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    tracer_provider = TracerProvider()
    exporter = OTLPSpanExporter(endpoint=endpoint)
    span_processor = BatchSpanProcessor(exporter)
    tracer_provider.add_span_processor(span_processor)
    trace_api.set_tracer_provider(tracer_provider)

    _config.trace_enabled = True


def get_tracer(
    name: str | None = None,
    version: str | None = None,
) -> trace_api.Tracer:
    """Get the tracer for the given name and version.

    Args:
        name (`str`):
            The name of the tracer.
        version (`str`):
            The version of the tracer.

    Returns:
        `trace_api.Tracer`: The tracer for the given name and version.
    """
    from .._version import __version__

    name = name or "agentscope"
    version = version or __version__
    return trace_api.get_tracer(name, version)
