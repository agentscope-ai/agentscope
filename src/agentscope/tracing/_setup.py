# -*- coding: utf-8 -*-
"""The tracing interface class in agentscope."""


def setup_tracing(
    endpoint: str,
    protocol: str = "http/protobuf",
) -> None:
    """Set up the AgentScope tracing by configuring the endpoint URL.

    Args:
        endpoint (`str`):
            The endpoint URL for the tracing exporter.
        protocol (`str`, optional):
            The protocol to use for the trace exporter. Supported values are:
            - "grpc": Use gRPC protocol
            - "http/protobuf": Use HTTP protocol with protobuf encoding
            Defaults to "http/protobuf".
    """
    # Lazy import
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry import trace

    tracer_provider = TracerProvider()
    if protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    exporter = OTLPSpanExporter(endpoint=endpoint)
    span_processor = BatchSpanProcessor(exporter)
    tracer_provider.add_span_processor(span_processor)
    trace.set_tracer_provider(tracer_provider)
