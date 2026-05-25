# -*- coding: utf-8 -*-
"""The unittests for tracing handling CancelledError."""
import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from agentscope import _config
from agentscope.tracing import trace


class TracingCancelledErrorTest(IsolatedAsyncioTestCase):
    """Test the tracing for handling CancelledError in async functions."""

    async def asyncSetUp(self) -> None:
        """Set up the test case"""

        self._original_trace_enabled = _config.trace_enabled
        _config.trace_enabled = True
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = provider.get_tracer(
            "tests.tracing_cancelled_error_test",
        )
        self.tracer_patcher = patch(
            "agentscope.tracing._trace._get_tracer",
            return_value=self.tracer,
        )
        self.tracer_patcher.start()
        self.addCleanup(self.tracer_patcher.stop)

    async def test_trace_ends_span_for_normal_exception(self) -> None:
        """Test that normal exceptions end the span with error status."""

        @trace(name="normal_exception_case")
        async def raise_value_error() -> None:
            raise ValueError("normal exception")

        with self.assertRaises(ValueError):
            await raise_value_error()

        finished_spans = self.exporter.get_finished_spans()

        self.assertEqual(len(finished_spans), 1)
        self.assertEqual(
            finished_spans[0].status.status_code,
            StatusCode.ERROR,
        )

    async def test_trace_should_end_span_for_cancelled_error(self) -> None:
        """Test that CancelledError ends the span with error status."""

        @trace(name="cancelled_error_case")
        async def raise_cancelled_error() -> None:
            raise asyncio.CancelledError("set cancelled error")

        with self.assertRaises(asyncio.CancelledError):
            await raise_cancelled_error()

        finished_spans = self.exporter.get_finished_spans()

        self.assertEqual(len(finished_spans), 1)
        self.assertEqual(
            finished_spans[0].status.status_code,
            StatusCode.ERROR,
        )

    async def asyncTearDown(self) -> None:
        """Restore tracing configuration after each test."""

        _config.trace_enabled = self._original_trace_enabled
