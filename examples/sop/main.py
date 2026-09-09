# -*- coding: utf-8 -*-
"""Turn a story outline into a stylised animation, one signed-off step at
a time.

Three milestones, and a person signs off on each: the storyboard and the
list of things to model, the animation Blender rendered, the restyled
video that goes out. How any of them gets done — how many shots, how the
rig is built, which filters make "sketch" look like a sketch — is the
agents' business.

Three things are worth watching for.

**Every verifier here is a person, and none of them holds anything open.**
:class:`HumanApproval` asks and stops. The stream ends, this program
blocks on ``input()`` with no agent suspended behind it, and the run
picks up when the answer arrives — a second later or a week.

**A refusal comes back as a critique.** Say ``n`` and give a reason; the
executor gets it verbatim on its next attempt, with which attempt it is.

**A step hands over an account, not its workspace.** Step two reads step
one's storyboard and modelling list; step three reads the path step two
rendered to. Nothing else crosses — no files, no context, no tools.

Prerequisites::

    export DASHSCOPE_API_KEY=sk-...
    uv tool install blender-mcp          # step two drives Blender over MCP
    # open Blender, enable the blender-mcp addon, start its server
    # ffmpeg on PATH                     # step three restyles the render

    python main.py
    python main.py --story "一只猫在雨夜的屋顶上追一片发光的落叶"
"""
import argparse
import asyncio
import os
import shutil
import subprocess
from typing import AsyncGenerator, Type

from pydantic import BaseModel

from agentscope.agent import Agent
from agentscope.console import ConsoleRenderer
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    AgentEvent,
    ConfirmResult,
    ExternalExecutionResultEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.message import (
    AssistantMsg,
    Msg,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.model import DashScopeChatModel
from agentscope.sop import SOP, SOPEngine, SOPPhase, SOPStep
from agentscope.tool import FunctionTool, Toolkit
from agentscope.types import ReplyFinishedReason
from agentscope._utils._common import _generate_id

DEFAULT_STORY = "清晨的森林里，一只小狐狸第一次学着自己抓鱼，最后和一只白鹭分享了收获。"

# Named looks, each an ffmpeg filter graph. A real pipeline would call a
# model here; a filter graph is enough to see the step's shape.
STYLES = {
    "sketch": "edgedetect=low=0.1:high=0.3,negate,hue=s=0",
    "vintage": (
        "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131,"
        "eq=contrast=0.9"
    ),
    "noir": "hue=s=0,eq=contrast=1.4:brightness=-0.05",
}


async def restyle_video(video_path: str, style: str) -> str:
    """Re-render a video in a named look and return the new file's path.

    Args:
        video_path (`str`):
            Path to the source video.
        style (`str`):
            One of ``sketch``, ``vintage``, ``noir``.
    """
    if style not in STYLES:
        raise ValueError(f"Unknown style {style!r}; pick from {list(STYLES)}")
    root, ext = os.path.splitext(video_path)
    out = f"{root}.{style}{ext or '.mp4'}"
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vf", STYLES[style], out],
        check=True,
        capture_output=True,
    )
    return out


class HumanApproval:
    """A verifier that is a person, and does not hold anything open.

    It satisfies the same protocol an :class:`~agentscope.agent.Agent`
    does, so the step cannot tell the difference: asked with nothing to
    go on, it posts its question and stops; asked again with an answer,
    it turns that answer into a verdict.
    """

    name = "reviewer"

    async def reply_stream(  # pylint: disable=unused-argument
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None = None,
        structured_schema: Type[BaseModel] | None = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Ask a person, or read what they answered."""
        if not isinstance(inputs, ExternalExecutionResultEvent):
            question = "\n\n".join(
                block.text
                for msg in (inputs or [])
                for block in msg.content
                if block.type == "text"
            )
            yield RequireExternalExecutionEvent(
                reply_id=_generate_id(),
                tool_calls=[
                    ToolCallBlock(
                        type="tool_call",
                        id=_generate_id(),
                        name="ask_user",
                        input=question,
                    ),
                ],
            )
            return

        answer = str(inputs.execution_results[0].output).strip()
        approved = answer.lower() in ("y", "yes", "是", "同意", "通过")
        yield AssistantMsg(
            name=self.name,
            content="",
            finished_reason=ReplyFinishedReason.COMPLETED,
            structured_output={
                "passed": approved,
                "message": "" if approved else answer,
            },
        )


async def build_sop(model_name: str, api_key: str, blender: MCPClient) -> SOP:
    """Assemble the procedure — agents, tools and all.

    At this layer a SOP is code: a step holds the agent that runs it and
    the one that judges it, both already built.
    """

    def model() -> DashScopeChatModel:
        return DashScopeChatModel(
            credential=DashScopeCredential(api_key=api_key),
            model=model_name,
        )

    director = Agent(
        name="director",
        system_prompt=(
            "You break a story into a shot list a 3D artist can build "
            "from. Number the shots; for each give the camera, the action "
            "and its length in seconds. Then list every character, animal "
            "and prop that has to be modelled, with a one-line look for "
            "each. Keep it under a minute of animation."
        ),
        model=model(),
    )
    animator = Agent(
        name="animator",
        system_prompt=(
            "You build and render animations in Blender through the tools "
            "you are given. Work from the shot list and modelling list "
            "exactly; do not invent shots. Render to an .mp4 and report "
            "its absolute path."
        ),
        model=model(),
        toolkit=Toolkit(tools=await blender.list_tools()),
    )
    colorist = Agent(
        name="colorist",
        system_prompt=(
            "You restyle a rendered video with the tool you are given. "
            "Pick the look that best fits the story, apply it, and report "
            "the absolute path of the result and why that look."
        ),
        model=model(),
        toolkit=Toolkit(tools=[FunctionTool(restyle_video)]),
    )

    return SOP(
        name="故事到风格化动画",
        description=(
            "Storyboard it, animate it in Blender, restyle the render."
        ),
        steps=[
            SOPStep(
                subject="分镜与建模需求",
                description=(
                    "Turn the story into a numbered shot list and a list "
                    "of everything that must be modelled — characters, "
                    "animals, props — each with a one-line description of "
                    "its look. Hand both over."
                ),
                executor=director,
                verifier=HumanApproval(),
                step_id="storyboard",
            ),
            SOPStep(
                subject="Blender 建模与动画",
                description=(
                    "Build the models on the list, animate the shots in "
                    "order, and render the whole thing to one .mp4. Hand "
                    "over the absolute path of the render and a line per "
                    "shot on what was built."
                ),
                executor=animator,
                verifier=HumanApproval(),
                step_id="animate",
            ),
            SOPStep(
                subject="视频风格化",
                description=(
                    "Restyle the render in the look that suits the story "
                    "and hand over the absolute path of the result, with "
                    "the look you chose and why."
                ),
                executor=colorist,
                verifier=HumanApproval(),
                step_id="restyle",
            ),
        ],
        sop_id="story-to-animation",
    )


async def answer_confirm(
    pending: RequireUserConfirmEvent,
) -> UserConfirmResultEvent:
    """Answer a tool-call confirmation an agent stopped on."""
    results = []
    for tool_call in pending.tool_calls:
        reply = await asyncio.to_thread(
            input,
            f"Allow '{tool_call.name}'? [y]es / [N]o ",
        )
        results.append(
            ConfirmResult(
                confirmed=reply.strip().lower() in ("y", "yes"),
                tool_call=tool_call,
            ),
        )
    return UserConfirmResultEvent(
        reply_id=pending.reply_id,
        confirm_results=results,
    )


async def answer_request(
    pending: RequireExternalExecutionEvent,
) -> ExternalExecutionResultEvent:
    """Answer a question something parked on — here, an approval.

    Nothing is suspended behind this prompt: the run let go of its stream
    before we got here, and picks up from its state afterwards.
    """
    results = []
    for tool_call in pending.tool_calls:
        print("\n" + "-" * 60)
        print(tool_call.input)
        print("-" * 60)
        reply = await asyncio.to_thread(input, "Approve? [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            reply = await asyncio.to_thread(input, "What should change? ")
        results.append(
            ToolResultBlock(
                id=tool_call.id,
                name=tool_call.name,
                output=reply.strip() or "Not approved as it stands.",
                state=ToolResultState.SUCCESS,
            ),
        )
    return ExternalExecutionResultEvent(
        reply_id=pending.reply_id,
        execution_results=results,
    )


async def main() -> None:
    """Run the procedure, pausing whenever a person is needed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.7-max")
    parser.add_argument("--story", default=DEFAULT_STORY)
    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("Set DASHSCOPE_API_KEY before running this demo.")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not on PATH; step three needs it.")

    blender = MCPClient(
        name="blender",
        mcp_config=StdioMCPConfig(command="uvx", args=["blender-mcp"]),
        is_stateful=True,
    )
    await blender.connect()
    try:
        sop = await build_sop(args.model, api_key, blender)
        engine = SOPEngine(sop)
        renderer = ConsoleRenderer()

        inputs: Msg | UserConfirmResultEvent | ExternalExecutionResultEvent
        inputs = UserMsg(name="user", content=args.story)
        while True:
            pending: AgentEvent | None = None
            async for event in engine.reply_stream(inputs):
                renderer.render(event)
                if isinstance(
                    event,
                    (RequireUserConfirmEvent, RequireExternalExecutionEvent),
                ):
                    pending = event

            if engine.phase is not SOPPhase.AWAITING:
                break
            if isinstance(pending, RequireUserConfirmEvent):
                inputs = await answer_confirm(pending)
            else:
                inputs = await answer_request(pending)

        print(f"\n== run {engine.phase.value}")
        for step in sop.steps:
            phase = engine.state.steps[step.id].phase.value
            print(f"   {step.subject}: {phase}")

        result = "".join(
            block.text
            for block in (engine.state.steps["restyle"].submission or [])
            if block.type == "text"
        )
        if result:
            print("\n" + "=" * 60)
            print("成片：\n")
            print(result)
    finally:
        await blender.close()


if __name__ == "__main__":
    asyncio.run(main())
