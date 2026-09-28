# -*- coding: utf-8 -*-
"""Try a read-only image tool and a confirmation-gated Otsu tool."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
from pydantic import BaseModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.console import launch_console
from agentscope.credential import CredentialBase, OpenAICredential
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import (
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)
from agentscope.model import ChatModelBase, ChatResponse, OpenAIChatModel

from examples.image_analysis.image_tools import ImageTools


class DemoCredential(CredentialBase):
    """Use an offline model without API credentials."""

    @classmethod
    def get_chat_model_class(cls) -> type[ChatModelBase]:
        """Return the scripted model class."""
        return ScriptedModel


class ScriptedModel(ChatModelBase):
    """Request known tool calls, then observe their real results."""

    class Parameters(BaseModel):
        """The offline model has no provider settings."""

    def __init__(self) -> None:
        super().__init__(
            credential=DemoCredential(),
            model="scripted-no-api",
            parameters=self.Parameters(),
            stream=False,
        )
        self.formatter = OpenAIChatFormatter()
        self.calls = 0
        self.results_seen: list[ToolResultBlock] = []
        self.requests = [
            ("inspect_image", {"image_name": "square.png"}),
            ("segment_otsu", {"image_name": "square.png"}),
            ("inspect_image", {"image_name": "missing.png"}),
        ]

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        **kwargs: Any,
    ) -> ChatResponse:
        """Return the next scripted request or a tool-result summary."""
        del model_name, kwargs
        self.calls += 1
        if self.calls <= len(self.requests):
            name, arguments = self.requests[self.calls - 1]
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id=f"image-call-{self.calls}",
                        name=name,
                        input=json.dumps(arguments),
                    ),
                ],
                is_last=True,
            )
        self.results_seen = [
            block
            for message in messages
            for block in message.get_content_blocks("tool_result")
        ]
        states = ", ".join(
            f"{result.name}={result.state}" for result in self.results_seen
        )
        return ChatResponse(
            content=[TextBlock(text=f"Observed tool results: {states}")],
            is_last=True,
        )


async def run_demo(work_dir: Path, approve: bool) -> dict:
    """Exercise permission pause and resume on a known image."""
    from PIL import Image

    run_dir = work_dir.resolve() / f"run-{uuid4().hex}"
    input_dir = run_dir / "images"
    output_dir = run_dir / "outputs"
    input_dir.mkdir(parents=True)
    gray = np.full((32, 32), 30, dtype=np.uint8)
    gray[8:24, 8:24] = 200
    Image.fromarray(gray).save(input_dir / "square.png")

    model = ScriptedModel()
    agent = Agent(
        name="image-assistant",
        system_prompt="Report measured image values; do not infer defects.",
        model=model,
        toolkit=ImageTools(input_dir, output_dir).toolkit(),
        injection_config=InjectionConfig(inject_runtime_state=False),
    )
    inputs = UserMsg(name="user", content="Analyze square.png")
    confirmations = []
    while True:
        pending = []
        async for event in agent.reply_stream(inputs):
            if isinstance(event, RequireUserConfirmEvent):
                pending.append(event)
        if not pending:
            break
        confirmations.extend(
            {
                "tool": call.name,
                "approved": approve,
                "outputs_before_decision": len(list(output_dir.glob("*.png"))),
            }
            for event in pending
            for call in event.tool_calls
        )
        inputs = UserConfirmResultEvent(
            reply_id=pending[0].reply_id,
            confirm_results=[
                ConfirmResult(confirmed=approve, tool_call=call)
                for event in pending
                for call in event.tool_calls
            ],
        )
    results = []
    for result in model.results_seen:
        output = result.output
        if isinstance(output, list):
            output = output[0].text if output else ""
        results.append(
            {
                "tool": result.name,
                "state": str(result.state),
                "output": (
                    json.loads(output)
                    if str(result.state) == "success"
                    else output
                ),
            },
        )
    return {
        "run_dir": str(run_dir),
        "confirmations": confirmations,
        "results": results,
        "output_files": [
            str(path) for path in sorted(output_dir.glob("*.png"))
        ],
    }


async def run_chat(model_name: str, input_dir: Path, output_dir: Path) -> None:
    """Launch the console using an OpenAI-compatible model API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Set OPENAI_API_KEY before starting chat mode")
    agent = Agent(
        name="image-assistant",
        system_prompt=(
            "Use the provided tools to inspect 8-bit images. "
            "Ask for a filename or ROI when needed. Report measurements "
            "and methods, not unvalidated defect-detection claims."
        ),
        model=OpenAIChatModel(
            credential=OpenAICredential(
                api_key=api_key,
                base_url=os.environ.get("OPENAI_BASE_URL") or None,
            ),
            model=model_name,
            stream=True,
        ),
        toolkit=ImageTools(input_dir, output_dir).toolkit(),
    )
    await launch_console(agent)


def main() -> None:
    """Select an offline acceptance demo or interactive chat."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    demo = subparsers.add_parser("demo", help="No API key required")
    demo.add_argument(
        "--decision",
        choices=("approve", "deny"),
        default="approve",
    )
    demo.add_argument(
        "--work-dir",
        type=Path,
        default=Path(__file__).parent / ".demo-output",
    )
    chat = subparsers.add_parser("chat", help="OpenAI-compatible console")
    chat.add_argument("--model", required=True)
    chat.add_argument("--input-dir", type=Path, required=True)
    chat.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "demo":
        report = asyncio.run(
            run_demo(args.work_dir, args.decision == "approve"),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        asyncio.run(run_chat(args.model, args.input_dir, args.output_dir))


if __name__ == "__main__":
    main()
