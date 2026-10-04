# -*- coding: utf-8 -*-
"""Run offline trajectory evaluation: python examples/evaluation/main.py."""
import argparse
import asyncio
from pathlib import Path
from typing import AsyncIterator

from agentscope.evaluation import (
    EvaluationCase,
    EvaluationRunner,
    TrajectoryEvaluator,
    write_jsonl,
)
from agentscope.event import (
    AgentEvent,
    ModelCallEndEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
)
from agentscope.message import AssistantMsg, Msg, ToolResultState, UserMsg


async def fixture_stream(
    case: EvaluationCase,
) -> AsyncIterator[AgentEvent | Msg]:
    """Yield deterministic events; both tools return successful results."""
    name = "weather" if case.id == "correct-tool" else "calculator"
    reply_id = case.id
    yield ReplyStartEvent(
        session_id="offline-demo",
        reply_id=reply_id,
        name="demo-agent",
    )
    yield ToolCallStartEvent(
        reply_id=reply_id,
        tool_call_id="call",
        tool_call_name=name,
    )
    yield ToolCallDeltaEvent(
        reply_id=reply_id,
        tool_call_id="call",
        delta='{"city":"London"}',
    )
    yield ToolCallEndEvent(reply_id=reply_id, tool_call_id="call")
    yield ToolResultStartEvent(
        reply_id=reply_id,
        tool_call_id="call",
        tool_call_name=name,
    )
    yield ToolResultEndEvent(
        reply_id=reply_id,
        tool_call_id="call",
        state=ToolResultState.SUCCESS,
    )
    yield ModelCallEndEvent(
        reply_id=reply_id,
        input_tokens=100,
        output_tokens=15,
    )
    yield ReplyEndEvent(session_id="offline-demo", reply_id=reply_id)
    yield AssistantMsg("demo-agent", "sunny", id=reply_id)


async def main(output: Path | None) -> None:
    """Compare execution outcome with a task-specific golden tool reference."""
    schemas = [
        {
            "type": "function",
            "function": {
                "name": name,
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
        for name in ("weather", "calculator")
    ]
    cases = [
        EvaluationCase(
            id=case_id,
            inputs=[UserMsg("user", "Weather in London?")],
            reference={"expected_tools": ["weather"], "final_text": "sunny"},
            tool_schemas=schemas,
        )
        for case_id in ("correct-tool", "wrong-tool")
    ]
    runner = EvaluationRunner(fixture_stream, [TrajectoryEvaluator()])
    results = await runner.run(cases)
    for result in results:
        metrics = {item.name: item.value for item in result.metrics}
        print(
            result.case_id,
            "result_success=",
            metrics["tool_result_success_rate"],
            "tool_selection=",
            metrics["tool_selection_accuracy"],
            "task_success=",
            metrics["task_success"],
        )
    print("Synthetic fixture; token counts are reported, unverified usage.")
    print(
        "Task success checks final text only; use a richer oracle as needed.",
    )
    if output is not None:
        write_jsonl(results, output)
        print("Saved:", output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    asyncio.run(main(parser.parse_args().output))
