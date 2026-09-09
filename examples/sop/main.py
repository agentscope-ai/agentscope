# -*- coding: utf-8 -*-
"""Turn one line of text into a stylised animation, one signed-off step at
a time — the shot everyone knows and nobody has seen: China lifting the
World Cup.

Three milestones, and a person signs off on each: the shot list with its
motion spelled out in frames, the animation Blender rendered from it, the
restyled video that goes out. How any of them gets done is the agents'
business.

Why Blender in the middle, rather than a video model straight from the
text: **motion you can dictate.** The captain's arms rise over exactly 40
frames, the camera pushes in at a fixed speed, the confetti falls under
real gravity. A diffusion model guesses at all of that; Blender does what
it is told.

So Blender renders a **white model** — untextured grey geometry, correct
motion, correct camera — and Wan 3.0 paints it. Physics from the one that
can be told; looks from the one that is good at looks.

Three things are worth watching for.

**Every verifier here is a person, and none of them holds anything open.**
:class:`HumanApproval` asks and stops. The stream ends, this program
blocks on ``input()`` with no agent suspended behind it, and the run
picks up when the answer arrives — a second later or a week.

**A refusal comes back as a critique.** Say ``n`` and give a reason; the
executor gets it verbatim on its next attempt, with which attempt it is.

**The agents share one workspace and hand over accounts, not files.**
Every render lands in ``workspace/``; what crosses between steps is the
path and a line on what was built.

Prerequisites::

    export DASHSCOPE_API_KEY=sk-...
    export DASHSCOPE_WORKSPACE_ID=llm-...     # 业务空间 ID, from the console
    brew install --cask blender
    # install Blender's own MCP add-on (addon/blender_mcp_addon in
    # https://projects.blender.org/lab/blender_mcp) and turn on its
    # auto-start; the MCP server itself is fetched from that repo by uvx

    python main.py
    python main.py --story "马里奥跳起顶碎砖块，金币弹出的那一下"
"""
import argparse
import asyncio
import os
import urllib.request
from http import HTTPStatus
from typing import AsyncGenerator, Type

import dashscope
import requests
from dashscope import VideoSynthesis
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
from agentscope.workspace import LocalWorkspace
from agentscope._utils._common import _generate_id

DEFAULT_STORY = "世界杯决赛终场哨响，中国队队长在队友的簇拥下走上领奖台，" + "双手举起大力神杯，彩带从天而降，看台上红旗翻涌。"


VIDEO_MODEL = "wan3.0-video"


def _upload(video_path: str, api_key: str) -> str:
    """Put a local file in Model Studio's temporary space, as an
    ``oss://`` URL good for 48 hours.

    A reference video has to be reachable by the service: unlike an
    image it cannot be inlined as base64, so a local render has to go
    somewhere first. The upload is bound to the model that will read it.
    """
    policy = requests.get(
        "https://dashscope.aliyuncs.com/api/v1/uploads",
        params={"action": "getPolicy", "model": VIDEO_MODEL},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    ).json()["data"]

    name = os.path.basename(video_path)
    key = f"{policy['upload_dir']}/{name}"
    with open(video_path, "rb") as handle:
        response = requests.post(
            policy["upload_host"],
            files={
                "OSSAccessKeyId": (None, policy["oss_access_key_id"]),
                "Signature": (None, policy["signature"]),
                "policy": (None, policy["policy"]),
                "x-oss-object-acl": (None, policy["x_oss_object_acl"]),
                "x-oss-forbid-overwrite": (
                    None,
                    policy["x_oss_forbid_overwrite"],
                ),
                "key": (None, key),
                "success_action_status": (None, "200"),
                "file": (name, handle),
            },
            timeout=600,
        )
    response.raise_for_status()
    return f"oss://{key}"


async def restyle_video(video_path: str, look: str) -> str:
    """Paint a white-model render in a described look, and return the
    path of the result.

    The render goes in as a reference video, so its motion, timing and
    framing are what come back; the look is what changes.

    Args:
        video_path (`str`):
            Absolute path to the render. At most 15 seconds, mp4 or mov,
            at least 16 fps — the model's own limits on a reference.
        look (`str`):
            What it should look like, e.g. ``"把整个画面改成 1990 年代
            手绘体育动画，赛璐璐上色，胶片颗粒"``.
    """
    api_key = os.environ["DASHSCOPE_API_KEY"]

    def run() -> str:
        task = VideoSynthesis.async_call(
            api_key=api_key,
            model=VIDEO_MODEL,
            prompt=look,
            media=[
                {
                    "type": "reference_video",
                    "url": _upload(video_path, api_key),
                },
            ],
            resolution="720P",
            ratio="adaptive",
            prompt_extend=True,
        )
        done = VideoSynthesis.wait(task=task, api_key=api_key)
        if done.status_code != HTTPStatus.OK:
            raise RuntimeError(f"{done.code}: {done.message}")
        out = f"{os.path.splitext(video_path)[0]}.styled.mp4"
        urllib.request.urlretrieve(done.output.video_url, out)
        return out

    return await asyncio.to_thread(run)


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


async def build_sop(
    workspace: LocalWorkspace,
    blender: MCPClient,
    model_name: str,
    api_key: str,
) -> SOP:
    """Assemble the procedure — agents, tools and all.

    At this layer a SOP is code: a step holds the agent that runs it and
    the one that judges it, both already built. The three agents share
    one workspace, so a render one of them writes is there for the next.
    """

    def model() -> DashScopeChatModel:
        return DashScopeChatModel(
            credential=DashScopeCredential(api_key=api_key),
            model=model_name,
        )

    shared = await workspace.list_tools()
    director = Agent(
        name="director",
        system_prompt=(
            "You are a storyboard artist who thinks in frames. Break the "
            "scene into numbered shots at 24 fps. For every shot give the "
            "camera (position, move, speed), each subject's motion as a "
            "start pose, end pose and frame range, and what physics "
            "applies — gravity on confetti, easing on a lift. Then list "
            "everything to model: people, props, crowd, with a one-line "
            "note on shape and scale — no colours or materials, a later "
            "step paints it. The whole thing must run under 15 seconds."
        ),
        model=model(),
        toolkit=Toolkit(tools=shared),
        offloader=workspace,
    )
    blocker = Agent(
        name="blocker",
        system_prompt=(
            "You block out animations in Blender through the tools you "
            "are given, as a white model: grey untextured geometry, "
            "right shapes, right motion, right camera. No materials, no "
            "lighting design — a later step paints all of that. Look up "
            "the bpy API with search_api_docs before writing code rather "
            "than guessing at it. Follow the shot list to the frame: "
            "keyframe exactly the ranges it gives and set the camera "
            "moves it specifies. Render one .mp4 under "
            f"{workspace.workdir}, at most 15 seconds and at least 24 "
            "fps, and report its absolute path."
        ),
        model=model(),
        toolkit=Toolkit(tools=[*shared, *await blender.list_tools()]),
        offloader=workspace,
    )
    colorist = Agent(
        name="colorist",
        system_prompt=(
            "You paint a white-model render with the tool you are given. "
            "Write the look as an instruction to a colourist — era, "
            "medium, palette, light, grain — in one or two sentences, in "
            "the language the scene is written in. The motion is already "
            "settled, so say nothing about it. Report the absolute path "
            "of the result and why that look."
        ),
        model=model(),
        toolkit=Toolkit(tools=[*shared, FunctionTool(restyle_video)]),
        offloader=workspace,
    )

    return SOP(
        name="文字到风格化动画",
        description=(
            "Storyboard it in frames, block it out in Blender, paint the "
            "white model."
        ),
        steps=[
            SOPStep(
                subject="分镜与建模需求",
                description=(
                    "Turn the scene into a numbered shot list with the "
                    "motion written in frames, and a list of everything "
                    "to model as bare geometry. Hand both over."
                ),
                executor=director,
                verifier=HumanApproval(),
                step_id="storyboard",
            ),
            SOPStep(
                subject="Blender 白膜动画",
                description=(
                    "Build what the list names as a white model, keyframe "
                    "the shots exactly as written, and render one .mp4 of "
                    "at most 15 seconds into the workspace. Hand over its "
                    "absolute path and a line per shot on what was built."
                ),
                executor=blocker,
                verifier=HumanApproval(),
                step_id="animate",
            ),
            SOPStep(
                subject="风格化上色",
                description=(
                    "Paint the white model in a look that suits the scene "
                    "and hand over the absolute path of the result, with "
                    "the look you chose and why."
                ),
                executor=colorist,
                verifier=HumanApproval(),
                step_id="restyle",
            ),
        ],
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
    workspace_id = os.environ.get("DASHSCOPE_WORKSPACE_ID")
    if not workspace_id:
        raise RuntimeError(
            "Set DASHSCOPE_WORKSPACE_ID — video generation is served on "
            "the workspace-scoped endpoint.",
        )
    # Beijing; the SDK reads this for the video model, while the chat
    # model carries its own URL.
    dashscope.base_http_api_url = (
        f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1"
    )
    here = os.path.dirname(os.path.abspath(__file__))
    blender = MCPClient(
        name="blender",
        mcp_config=StdioMCPConfig(
            command="uvx",
            args=[
                "--from",
                "git+https://projects.blender.org/lab/blender_mcp.git"
                "#subdirectory=mcp",
                "blender-mcp",
            ],
        ),
        is_stateful=True,
    )
    await blender.connect()
    try:
        async with LocalWorkspace(
            workdir=os.path.join(here, "workspace"),
        ) as workspace:
            sop = await build_sop(workspace, blender, args.model, api_key)
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
                        (
                            RequireUserConfirmEvent,
                            RequireExternalExecutionEvent,
                        ),
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
