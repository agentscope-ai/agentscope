# -*- coding: utf-8 -*-
"""The tracing interface class in agentscope."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer
else:
    Tracer = "Tracer"


def setup_tracing(endpoint: str) -> None:
    """Set up the AgentScope tracing by configuring the endpoint URL.

    Args:
        endpoint (`str`):
            The endpoint URL for the tracing exporter.
    """
    # Lazy import
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    # Prepare a span_processor
    exporter = OTLPSpanExporter(endpoint=endpoint)
    span_processor = BatchSpanProcessor(exporter)

    tracer_provider: TracerProvider = trace.get_tracer_provider()
    if isinstance(tracer_provider, TracerProvider):
        # The provider is set outside, just add the span processor
        tracer_provider.add_span_processor(span_processor)

    else:
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(span_processor)
        trace.set_tracer_provider(tracer_provider)


def _get_tracer() -> Tracer:
    """Get the tracer
    Returns:
        `Tracer`: The tracer with the name "agentscope" and version.
    """
    from opentelemetry import trace
    from .._version import __version__

    return trace.get_tracer("agentscope", __version__)
