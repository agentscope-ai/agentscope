# -*- coding: utf-8 -*-
"""The tracing interface class in agentscope."""


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
    from opentelemetry import trace

    tracer_provider = TracerProvider()
    exporter = OTLPSpanExporter(endpoint=endpoint)
    span_processor = BatchSpanProcessor(exporter)
    tracer_provider.add_span_processor(span_processor)
    trace.set_tracer_provider(tracer_provider)
