# -*- coding: utf-8 -*-
"""An in memory exporter of OpenTelemetry traces for AgentScope evaluator, used
to record the token usage during evaluation."""
import json
from collections import defaultdict
from typing import Sequence

from opentelemetry import baggage
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from ...tracing._types import SpanKind, SpanAttributes


class _InMemoryExporter(SpanExporter):
    """An in memory exporter to store the token usage from the ChatModel spans
    in OpenTelemetry traces."""

    def __init__(self) -> None:
        """Initialize the in memory exporter."""
        # Initialize the counter
        self.cnt: dict = {}
        self._stopped = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Exports a batch of telemetry data.

        Args:
            spans (`Sequence[ReadableSpan]`):
                The list of `opentelemetry.trace.Span` objects to be exported

        Returns:
            `SpanExportResult`:
                The result of the export
        """
        for span in spans:
            task_id = baggage.get_baggage("task_id")
            repeat_id = baggage.get_baggage("repeat_id")

            if task_id is None or repeat_id is None:
                continue

            if task_id not in self.cnt:
                self.cnt[task_id] = {}

            if repeat_id not in self.cnt[task_id]:
                self.cnt[task_id][repeat_id] = {
                    "llm": defaultdict(int),
                    "agent": 0,
                    "tool": defaultdict(int),
                    "embedding": defaultdict(int),
                    "chat_usage": {},
                }

            span_kind = json.loads(
                span.attributes.get(SpanAttributes.SPAN_KIND, '""'),
            )
            if span_kind == SpanKind.LLM:
                metadata = json.loads(span.attributes.get("metadata", "{}"))
                model_name = metadata.get("model_name", "unknown")
                self.cnt[task_id][repeat_id]["llm"][model_name] += 1
                if (
                    model_name
                    not in self.cnt[task_id][repeat_id]["chat_usage"]
                ):
                    self.cnt[task_id][repeat_id]["chat_usage"][
                        model_name
                    ] = defaultdict(int)

                output = json.loads(span.attributes.get("output", "{}"))
                usage = output.get("usage")
                if "input_tokens" in usage:
                    self.cnt[task_id][repeat_id]["chat_usage"][model_name][
                        "input_tokens"
                    ] += usage["input_tokens"]
                if "output_tokens" in usage:
                    self.cnt[task_id][repeat_id]["chat_usage"][model_name][
                        "output_tokens"
                    ] += usage["output_tokens"]

            elif span_kind == SpanKind.AGENT:
                self.cnt[task_id][repeat_id]["agent"] += 1

            elif span_kind == SpanKind.TOOL:
                tool_name = json.loads(span.attributes.get("metadata")).get(
                    "name",
                    "unknown",
                )
                self.cnt[task_id][repeat_id]["tool"][tool_name] += 1

            elif span_kind == SpanKind.EMBEDDING:
                embedding_model = json.loads(
                    span.attributes.get("metadata"),
                ).get("name", "unknown")
                self.cnt[task_id][repeat_id]["embedding"][embedding_model] += 1

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """Shuts down the exporter."""
        self._stopped = True
