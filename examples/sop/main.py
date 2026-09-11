# -*- coding: utf-8 -*-
"""Turn one line of text into a stylised animation, one signed-off step at
a time — a ship crossing a planet, alone between the stars.

Three milestones, and a person signs off on each: the shot list with its
motion spelled out in frames, the animation Blender rendered from it, the
restyled video that goes out. How any of them gets done is the agents'
business.

Why Blender in the middle, rather than a video model straight from the
text: **motion you can dictate.** The ship drifts across frame at a fixed
speed, the camera pushes in over exactly 40 frames, the planet keeps its
scale against the hull. A diffusion model guesses at all of that; Blender
does what it is told.

So Blender renders a **white model** — untextured grey geometry, correct
motion, correct camera — and Wan 3.0 paints it. Physics from the one that
can be told; looks from the one that is good at looks.

Three things are worth watching for.

**Every verifier here is a person, and none of them holds anything open.**
:class:`HumanApproval` asks and stops. The stream ends with no agent
suspended behind it, and the run picks up when the answer arrives — a
second later or a week.

**The whole thing runs in the terminal UI.** A SOP engine is a pipeline,
so :func:`~agentscope.tui.launch_tui` drives it exactly as it drives an
agent; the verifier asks through the standard
:class:`~agentscope.tool.AskUser` tool and the UI draws the form.

**A refusal comes back as a critique.** Pick *Send back* and say why; the
executor gets that verbatim on its next attempt, with which attempt it is.

**The agents share one workspace and hand over accounts, not files.**
Every render lands in ``workspace/``; what crosses between steps is the
path and a line on what was built.

Prerequisites::

    export DASHSCOPE_API_KEY=sk-...
    python setup_blender.py   # once — Blender 5.1+ and its MCP add-on

    python main.py
    python main.py --blender /path/to/blender

Then describe a scene in the composer, e.g. ``马里奥跳起顶碎砖块，金币弹
出的那一下``.

Blender is started headless here and shut down on the way out. Leave one
open with the add-on connected and that one is used instead, so you can
watch the scene being built.
"""
import argparse
import asyncio
import contextlib
import os
import shutil
import socket
import subprocess
import time
from typing import AsyncGenerator, Iterator, Type

from pydantic import BaseModel
from wan_video import restyle_video

from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    AgentEvent,
    ExternalExecutionResultEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.message import AssistantMsg, Msg, ToolCallBlock
from agentscope.model import DashScopeChatModel
from agentscope.sop import SOP, SOPEngine, SOPStep
from agentscope.tool import (
    AskUser,
    AskUserMetadata,
    AskUserParams,
    FunctionTool,
    Toolkit,
)
from agentscope.tui import launch_tui
from agentscope.types import ReplyFinishedReason
from agentscope.workspace import LocalWorkspace
from agentscope._utils._common import _generate_id


BLENDER_PORT = 9876


def _blender_listening() -> bool:
    """Whether Blender's MCP add-on has a socket up."""
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", BLENDER_PORT)) == 0


@contextlib.contextmanager
def blender_running(blender: str | None) -> Iterator[None]:
    """Make sure something is listening on Blender's MCP port.

    A Blender that is already up is used as it is — leave the GUI open
    and you can watch the scene being built. Otherwise one is started
    headless here, and killed on the way out, so the only thing this
    demo asks of the machine is that Blender be installed.
    """
    if _blender_listening():
        yield
        return
    if blender is None:
        raise RuntimeError(
            "Blender 5.1+ with its MCP add-on is needed. Put it on PATH "
            "or pass --blender /path/to/blender.",
        )

    # Its own session, so Ctrl+C reaches this program alone and Blender
    # goes down through the terminate() below.
    process = subprocess.Popen(  # pylint: disable=consider-using-with
        [
            blender,
            "--background",
            "--online-mode",
            "--command",
            "blender_mcp",
            "--port",
            str(BLENDER_PORT),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        for _ in range(150):
            if _blender_listening():
                break
            if process.poll() is not None:
                raise RuntimeError(
                    f"'{blender} --command blender_mcp' exited with "
                    f"{process.returncode}. Blender 5.1+ with the MCP "
                    f"add-on installed and enabled is required.",
                )
            time.sleep(0.1)
        else:
            raise RuntimeError("Blender never opened its MCP port.")
        yield
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)


class HumanApproval:
    """A verifier that is a person, and does not hold anything open.

    It satisfies the same protocol an :class:`~agentscope.agent.Agent`
    does, so the step cannot tell the difference: asked with nothing to
    go on, it posts its question and stops; asked again with an answer,
    it turns that answer into a verdict.

    It asks through :class:`~agentscope.tool.AskUser` — not because a
    model chose that tool, but because the name and its schema are what
    every front end already knows how to draw. The answer comes back in
    ``metadata``, shaped by :class:`~agentscope.tool.AskUserMetadata`,
    so the verdict is read rather than guessed at.
    """

    name = "reviewer"

    def __init__(self) -> None:
        self.reply_id = ""

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
            self.reply_id = _generate_id()
            # A reply of its own, so the question and the answer land in
            # one bubble under this name rather than an anonymous one.
            yield ReplyStartEvent(
                session_id="sop",
                reply_id=self.reply_id,
                name=self.name,
            )
            yield RequireExternalExecutionEvent(
                reply_id=self.reply_id,
                tool_calls=[
                    ToolCallBlock(
                        type="tool_call",
                        id=_generate_id(),
                        name=AskUser.name,
                        input=AskUserParams(
                            questions=[
                                {
                                    "question": "Approve this and move on?",
                                    "header": "Review",
                                    "context": "\n\n".join(
                                        block.text
                                        for msg in (inputs or [])
                                        for block in msg.content
                                        if block.type == "text"
                                    ),
                                    "options": [
                                        {
                                            "label": "Approve",
                                            "description": (
                                                "Hand it on to the next step."
                                            ),
                                        },
                                        {
                                            "label": "Send back",
                                            "description": (
                                                "Say what should change; the "
                                                "step tries again."
                                            ),
                                        },
                                    ],
                                },
                            ],
                        ).model_dump_json(),
                    ),
                ],
            )
            return

        result = inputs.execution_results[0]
        await AskUser().check_external_result(result)
        answer = AskUserMetadata.model_validate(result.metadata).answers[0]
        approved = answer.selected == ["Approve"]
        yield ReplyEndEvent(session_id="sop", reply_id=self.reply_id)
        yield AssistantMsg(
            name=self.name,
            content="",
            finished_reason=ReplyFinishedReason.COMPLETED,
            structured_output={
                "passed": approved,
                # Whatever they typed instead of picking is the critique.
                "message": "" if approved else (answer.other or "Sent back."),
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

    director = Agent(
        name="director",
        system_prompt=(
            "You are a storyboard artist who thinks in frames. Break the "
            "scene into numbered shots at 24 fps. For every shot give the "
            "camera (position, move, speed), each subject's motion as a "
            "start pose, end pose and frame range, and what physics "
            "applies — inertia on a drift, easing on a push-in. Then list "
            "everything to model: people, props, crowd, with a one-line "
            "note on shape and scale — no colours or materials, a later "
            "step paints it. The whole thing must run under 15 seconds."
        ),
        model=model(),
        toolkit=Toolkit(tools=await workspace.list_tools()),
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
            "fps, and report its absolute path.\n\n"
            # Four facts about this setup, each of which otherwise costs
            # an attempt to rediscover.
            "This Blender runs headless, which makes four things true:\n"
            "- Render with Cycles on the CPU. EEVEE needs a GPU surface "
            "here and takes the whole process down with it.\n"
            "- For video output set `image_settings.media_type` to "
            "'VIDEO' before `file_format` to 'FFMPEG'. Doing it the 4.x "
            'way raises `enum "FFMPEG" not found` on Blender 5.\n'
            "- Rendering often answers `Empty response from Blender`. "
            "That is the call outrunning the render, not a failure — "
            "check whether the file appeared before you retry.\n"
            "- Use `execute_blender_code` to reach this running Blender. "
            "`execute_blender_code_for_cli` starts a separate one from a "
            "`.blend` file and will not see your scene."
        ),
        model=model(),
        toolkit=Toolkit(tools=await workspace.list_tools(), mcps=[blender]),
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
        toolkit=Toolkit(
            tools=await workspace.list_tools() + [FunctionTool(restyle_video)],
        ),
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


async def main() -> None:
    """Run the procedure, pausing whenever a person is needed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.7-max")
    parser.add_argument("--blender", default=shutil.which("blender"))
    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("Set DASHSCOPE_API_KEY before running this demo.")
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
    async with contextlib.AsyncExitStack() as stack:
        stack.enter_context(blender_running(args.blender))
        await blender.connect()
        stack.push_async_callback(blender.close)
        workspace = await stack.enter_async_context(
            LocalWorkspace(workdir=os.path.join(here, "workspace")),
        )

        sop = await build_sop(workspace, blender, args.model, api_key)
        engine = SOPEngine(sop)

        # The engine is a pipeline, so the TUI drives it exactly as it
        # drives an agent: approvals park the run, the stream ends, and
        # the answer starts it again. Describe the scene in the composer
        # to begin.
        await launch_tui(engine)

    print(f"\n== run {engine.phase.value}")
    for step in sop.steps:
        print(f"   {step.subject}: {engine.state.steps[step.id].phase.value}")

    result = "".join(
        block.text
        for block in (engine.state.steps["restyle"].submission or [])
        if block.type == "text"
    )
    if result:
        print("\n" + "=" * 60)
        print("成片：\n")
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
