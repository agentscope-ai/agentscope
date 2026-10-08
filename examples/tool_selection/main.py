# -*- coding: utf-8 -*-
"""Offline tool selection demo and synthetic retrieval measurements."""
import argparse
import asyncio
import json
import re
import statistics
import time
from typing import Any

from agentscope.agent import Agent, InjectionConfig
from agentscope.credential import CredentialBase
from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, UserMsg
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.tool import (
    EmbeddingToolSelector,
    FunctionTool,
    ToolChoice,
    ToolChunk,
    Toolkit,
)


class FixtureEmbedding(EmbeddingModelBase[str]):
    """Encode explicit capability IDs for deterministic protocol testing.

    This fixture deliberately makes retrieval easy. Its recall and latency
    are not estimates of production embedding quality or network latency.
    """

    def __init__(self) -> None:
        """Create an offline 101-dimensional embedding model."""
        super().__init__(
            credential=CredentialBase(),
            model="synthetic-capability-fixture",
            dimensions=101,
            parameters=None,
            context_size=8192,
            batch_size=128,
            max_retries=0,
            retry_delay=0,
        )
        self.embedded_inputs = 0

    async def _call_api(self, inputs: list[str]) -> EmbeddingResponse:
        """Return a vector for each explicit capability identifier."""
        self.embedded_inputs += len(inputs)
        vectors = []
        for text in inputs:
            vector = [0.0] * self.dimensions
            matches = re.findall(r"capability_(\d{3})", text)
            for match in matches:
                vector[int(match)] = 1.0
            if not matches:
                vector[-1] = 1.0
            vectors.append(vector)
        return EmbeddingResponse(embeddings=vectors)


class InspectingChatModel(ChatModelBase):
    """Show model-visible schemas without a network call or generated plan."""

    def __init__(self) -> None:
        """Create a model that uses the framework's input token estimator."""
        super().__init__(
            credential=CredentialBase(),
            model="offline-inspector",
            parameters=self.Parameters(),
            stream=False,
            context_size=32768,
        )
        self.formatter = OpenAIChatFormatter()

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Return the schema names seen by the actual model call."""
        names = [tool["function"]["name"] for tool in tools or []]
        return ChatResponse(
            content=[
                TextBlock(text=f"Model-visible tools: {', '.join(names)}"),
            ],
            is_last=True,
        )


def fixture_tool(query: str) -> ToolChunk:
    """Return a harmless local response if a tool is invoked.

    Args:
        query (`str`):
            Synthetic lookup query.
    """
    return ToolChunk(content=[TextBlock(text=f"Fixture result: {query}")])


def make_toolkit(size: int) -> Toolkit:
    """Create size tools, including one required control tool."""
    tools = [
        FunctionTool(
            fixture_tool,
            name="audit_notes",
            description="Record a local audit note for this workflow.",
            is_read_only=True,
        ),
    ]
    tools.extend(
        FunctionTool(
            fixture_tool,
            name=f"lookup_{index:03d}",
            description=f"Look up records for capability_{index:03d}.",
            is_read_only=True,
        )
        for index in range(size - 1)
    )
    return Toolkit(tools=tools)


async def demo() -> None:
    """Exercise the public Agent integration and expose its diagnostics."""
    agent = Agent(
        name="offline-selector-demo",
        system_prompt="Inspect the tools selected for this synthetic task.",
        model=InspectingChatModel(),
        toolkit=make_toolkit(20),
        tool_selector=EmbeddingToolSelector(FixtureEmbedding(), top_k=3),
        max_tool_schema_tokens=450,
        required_tools=("audit_notes",),
        injection_config=InjectionConfig(inject_runtime_state=False),
    )
    response = await agent.reply(
        UserMsg("user", "Look up capability_017 and capability_004."),
    )
    print(response.get_text_content())
    selection = agent.last_tool_selection
    if selection is None:
        raise RuntimeError("The agent did not produce a tool selection.")
    print(
        json.dumps(
            {
                "kind": "agent_demo",
                "schema_tokens_estimated": selection.schema_tokens,
                "used_fallback": selection.used_fallback,
                "budget_exceeded": selection.budget_exceeded,
            },
        ),
    )


async def measure(size: int, task_count: int) -> dict:
    """Measure golden tool recall, schema cost and local cold/warm latency."""
    tools = await make_toolkit(size).get_tool_schemas()
    model = InspectingChatModel()
    embedding = FixtureEmbedding()
    selector = EmbeddingToolSelector(embedding, top_k=3)

    async def count_tokens(schemas: list[dict]) -> int:
        """Use the same framework counter as the model integration."""
        return await model.count_tokens([], schemas)

    recalls = []
    costs = []
    cold_times = []
    warm_times = []
    cold_inputs = []
    warm_inputs = []
    for index in range(task_count):
        targets = {index % (size - 1), (index + 7) % (size - 1)}
        query = "Look up " + " and ".join(
            f"capability_{target:03d}" for target in sorted(targets)
        )
        golden = {f"lookup_{target:03d}" for target in targets}
        selector.clear_cache()
        for label in ("cold", "warm"):
            inputs_before = embedding.embedded_inputs
            started = time.perf_counter()
            selection = await selector.select(
                query,
                tools,
                count_tokens=count_tokens,
                max_tokens=450,
                required_tools=("audit_notes",),
            )
            elapsed = (time.perf_counter() - started) * 1000
            inputs = embedding.embedded_inputs - inputs_before
            if label == "cold":
                cold_times.append(elapsed)
                cold_inputs.append(inputs)
                names = {tool["function"]["name"] for tool in selection.tools}
                recalls.append(len(names & golden) / len(golden))
                costs.append(selection.schema_tokens)
            else:
                warm_times.append(elapsed)
                warm_inputs.append(inputs)
    return {
        "kind": "synthetic_protocol_measurement",
        "tools": size,
        "tasks": task_count,
        "optional_top_k": 3,
        "required_tools": 1,
        "mean_golden_tool_recall": statistics.mean(recalls),
        "full_schema_tokens_estimated": await count_tokens(tools),
        "selected_schema_tokens_mean_estimated": statistics.mean(costs),
        "cold_latency_mean_ms": round(statistics.mean(cold_times), 3),
        "warm_latency_mean_ms": round(statistics.mean(warm_times), 3),
        "cold_embedded_inputs_mean": statistics.mean(cold_inputs),
        "warm_embedded_inputs_mean": statistics.mean(warm_inputs),
        "task_success_rate": None,
    }


async def main(task_count: int) -> None:
    """Run a local integration demonstration and three synthetic datasets."""
    print("Offline synthetic fixtures; no real LLM task success is measured.")
    await demo()
    for size in (20, 50, 100):
        print(json.dumps(await measure(size, task_count)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=30)
    arguments = parser.parse_args()
    if arguments.tasks < 1:
        parser.error("--tasks must be positive")
    asyncio.run(main(arguments.tasks))
