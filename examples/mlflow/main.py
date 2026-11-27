# -*- coding: utf-8 -*-
"""Export AgentScope agent, model, and tool traces to an MLflow experiment."""
import argparse
import asyncio
import os
import sys

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.message import Msg, UserMsg
from agentscope.middleware import TracingMiddleware
from agentscope.model import ChatModelBase, OpenAIChatModel
from agentscope.tool import FunctionTool, Toolkit


def add(a: int, b: int) -> str:
    """Add two integers.

    Args:
        a (int): The first integer.
        b (int): The second integer.

    Returns:
        str: The sum.
    """
    return str(a + b)


def configure_tracing(endpoint: str, experiment_id: str) -> TracerProvider:
    """Install the standalone example's provider and return it for cleanup."""
    provider = TracerProvider()
    exporter = OTLPSpanExporter(
        endpoint=endpoint,
        headers={"x-mlflow-experiment-id": experiment_id},
        timeout=5,
        # Compressed OTLP requests require MLflow 3.7 or newer.
        compression=Compression.NoCompression,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider


async def run_agent(model: ChatModelBase) -> Msg:
    """Run a small tool-using conversation with existing tracing middleware."""
    agent = Agent(
        name="mlflow_demo",
        system_prompt=(
            "Use the add tool for arithmetic, then report its result."
        ),
        model=model,
        toolkit=Toolkit(tools=[FunctionTool(add, is_read_only=True)]),
        middlewares=[TracingMiddleware()],
    )
    return await agent.reply(UserMsg("user", "Use add to calculate 17 + 25."))


def main() -> None:
    """Run the agent and drain the process-owned exporter."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument(
        "--traces-endpoint",
        default="http://127.0.0.1:5000/v1/traces",
    )
    parser.add_argument("--model", default="gpt-4.1-mini")
    args = parser.parse_args()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        parser.error("Set OPENAI_API_KEY before running this example.")

    provider = configure_tracing(args.traces_endpoint, args.experiment_id)
    try:
        model = OpenAIChatModel(
            credential=OpenAICredential(
                api_key=api_key,
                base_url=os.environ.get("OPENAI_BASE_URL"),
            ),
            model=args.model,
            stream=False,
        )
        response = asyncio.run(run_agent(model))
        print(response.get_text_content())
    finally:
        if not provider.force_flush(timeout_millis=5000):
            print("Timed out draining the trace queue.", file=sys.stderr)
        provider.shutdown()


if __name__ == "__main__":
    main()
