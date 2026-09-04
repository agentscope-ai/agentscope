# -*- coding: utf-8 -*-
"""Run a three-step SOP: outline a note, write it, announce it.

Two things this demo is really about.

**The handover is text.** The first two steps share one agent, so they
share its context and its files. The third is a different agent with no
tools at all — it can only see what step two *submitted*, which is the
point: a SOP hands prose between steps, never a workspace.

**Waiting costs nothing.** The last step is signed off by a person, and
its verifier says so by answering nothing at all. The engine ends the
stream rather than holding a coroutine open — this program then blocks on
``input()`` with no agent suspended anywhere behind it — and picks the run
back up when an answer arrives.

Run with::

    export DASHSCOPE_API_KEY=sk-...
    python main.py [--topic "..."] [--model qwen3.7-max]
"""
import argparse
import asyncio
import os

from agentscope.agent import Agent
from agentscope.console import ConsoleRenderer
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.message import TextBlock
from agentscope.model import DashScopeChatModel
from agentscope.permission import PermissionMode
from agentscope.sop import (
    SOP,
    RunSettledEvent,
    SOPEngine,
    SOPRunStatus,
    SOPStep,
    SOPStepState,
    StepStateEvent,
    VerificationRecord,
    VerifierBase,
)
from agentscope.state import Task
from agentscope.tool import Toolkit
from agentscope.workspace import LocalWorkspace

NOTE = "note.md"


class FileWritten(VerifierBase):
    """Accepts a step once a file has appeared in the workspace.

    It takes the workspace at construction rather than being handed one,
    which is the rule everything at this layer follows: the engine never
    learns what a workspace is, so it stays runnable without a service
    underneath.
    """

    def __init__(self, workspace: LocalWorkspace, path: str) -> None:
        """Remember what to look for, and where.

        Resolved against the workspace root rather than the backend's
        working directory, which is wherever the process happens to be.
        """
        self._backend = workspace.get_backend()
        self._path = self._backend.join_path(workspace.workdir, path)

    async def verify(self, sop, run, step, step_run) -> VerificationRecord:
        """Look for the file, and say what is missing if it is."""
        found = await self._backend.file_exists(self._path)
        return VerificationRecord(
            passed=found,
            message="" if found else f"{self._path} is not there yet.",
            verified_by="file-check",
        )


class HumanApproval(VerifierBase):
    """Waits for a person, without waiting.

    Answering ``None`` is how a verifier says "not yet". The step stays in
    ``VERIFYING``, the engine lets go of the stream, and the run is asked
    again once :attr:`answer` has been filled in — which may be a second
    later or a week.
    """

    def __init__(self) -> None:
        """Start with no answer."""
        self.answer: VerificationRecord | None = None

    async def verify(self, sop, run, step, step_run):
        """Hand over an answer if one has arrived, otherwise nothing."""
        answer, self.answer = self.answer, None
        return answer


async def build_sop(
    workspace: LocalWorkspace,
    model_name: str,
    api_key: str,
) -> SOP:
    """Assemble the procedure — agents, verifiers and all.

    At this layer a SOP is code: a step holds the agent that runs it and
    the verifier that judges it, both already built. There is no id to
    resolve and no spec to materialise.
    """

    def chat_model() -> DashScopeChatModel:
        return DashScopeChatModel(
            credential=DashScopeCredential(api_key=api_key),
            model=model_name,
            stream=True,
        )

    writer = Agent(
        name="writer",
        system_prompt=(
            "You write short, concrete technical notes. Keep them under "
            "300 words and skip the throat-clearing."
        ),
        model=chat_model(),
        toolkit=Toolkit(tools=await workspace.list_tools()),
        offloader=workspace,
    )
    # Let the writer edit inside the workspace without a prompt per file;
    # this demo is about acceptance, not about tool permissions.
    writer.state.permission_context.mode = PermissionMode.ACCEPT_EDITS
    writer.state.permission_context.working_directories["demo"] = (
        workspace.workdir
    )

    editor = Agent(
        name="editor",
        system_prompt=(
            "You turn a technical note into a two-sentence announcement "
            "for a team chat. You have no tools and no files — work only "
            "from what you were handed."
        ),
        model=chat_model(),
    )

    note_path = os.path.join(workspace.workdir, NOTE)

    return SOP(
        name="Write and announce a note",
        description="Outline a note, write it to disk, announce it.",
        steps=[
            SOPStep(
                id="outline",
                subject="Outline the note",
                description=(
                    "Decide what the note should cover. Submit the "
                    "outline as a short bulleted list."
                ),
                agent=writer,
                # Seeding the agent's own task list narrows how it
                # decomposes the step without taking the decision away.
                tasks=[
                    Task(
                        subject="Decide the audience",
                        description="Who is this note for?",
                        metadata={},
                    ),
                    Task(
                        subject="Pick three points",
                        description="No more than three.",
                        metadata={},
                    ),
                ],
            ),
            SOPStep(
                id="draft",
                subject="Write the note",
                description=(
                    f"Write the note to {note_path}, following the "
                    "outline you were handed."
                ),
                agent=writer,
                blocked_by=["outline"],
                verifier=FileWritten(workspace, NOTE),
            ),
            SOPStep(
                id="announce",
                subject="Announce it",
                description=(
                    "Write a two-sentence announcement for a team chat."
                ),
                # A different agent, with no tools: all it can see is what
                # the previous step submitted.
                agent=editor,
                blocked_by=["draft"],
                verifier=HumanApproval(),
            ),
        ],
    )


def show(event: object, renderer: ConsoleRenderer) -> None:
    """Print SOP events plainly and let the renderer handle the rest."""
    if isinstance(event, StepStateEvent):
        line = f"\n── {event.subject} · {event.state.value}"
        if event.message:
            line += f" — {event.message}"
        print(line)
    elif isinstance(event, RunSettledEvent):
        print(f"\n══ run {event.status.value} {event.reason}".rstrip())
    else:
        renderer.render(event)


async def ask_permission(
    pending: RequireUserConfirmEvent,
) -> UserConfirmResultEvent:
    """Answer a tool-call confirmation an agent stopped on.

    The reply an answer belongs to names itself, and the engine hands it
    to whichever step's agent is waiting on that reply — so a caller never
    has to work out which of them asked.
    """
    results = []
    for tool_call in pending.tool_calls:
        answer = await asyncio.to_thread(
            input,
            f"Allow '{tool_call.name}'? [y]es / [N]o ",
        )
        results.append(
            ConfirmResult(
                confirmed=answer.strip().lower() in ("y", "yes"),
                tool_call=tool_call,
            ),
        )
    return UserConfirmResultEvent(
        reply_id=pending.reply_id,
        confirm_results=results,
    )


async def ask_for_signoff(engine: SOPEngine) -> VerificationRecord | None:
    """Show what is waiting on a person and read a verdict.

    Answers ``None`` when nothing is actually waiting to be judged, which
    is not an error — an agent may simply have stopped for permission.
    """
    waiting = next(
        (
            step
            for step in engine.sop.steps
            if engine.run.steps[step.id].state is SOPStepState.VERIFYING
        ),
        None,
    )
    if waiting is None:
        return None

    print(f"\n{'-' * 60}")
    print(f"{waiting.subject} is waiting for you:\n")
    print(engine.run.steps[waiting.id].submission)
    print("-" * 60)

    verdict = await asyncio.to_thread(input, "Accept it? [y/N] ")
    if verdict.strip().lower() in ("y", "yes"):
        return VerificationRecord(passed=True, verified_by="you")
    reason = await asyncio.to_thread(input, "What should change? ")
    return VerificationRecord(
        passed=False,
        message=reason.strip() or "Try again.",
        verified_by="you",
    )


async def main() -> None:
    """Run the SOP, pausing whenever a person is needed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.7-max")
    parser.add_argument(
        "--topic",
        default="Why our SOP steps hand over text instead of files",
    )
    parser.add_argument(
        "--workdir",
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "workspace",
        ),
    )
    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set the DASHSCOPE_API_KEY environment variable before "
            "running this demo.",
        )

    async with LocalWorkspace(workdir=args.workdir) as workspace:
        sop = await build_sop(workspace, args.model, api_key)
        engine = SOPEngine(sop)
        renderer = ConsoleRenderer()
        approval: HumanApproval = sop.steps[-1].verifier

        inputs: list | UserConfirmResultEvent | None = [
            TextBlock(text=args.topic),
        ]
        while True:
            # A run stops for two different reasons, told apart by what
            # came out of the stream rather than by asking around.
            asked_permission: RequireUserConfirmEvent | None = None
            async for event in engine.run_stream(inputs):
                if isinstance(event, RequireUserConfirmEvent):
                    asked_permission = event
                show(event, renderer)
            inputs = None

            if engine.status is not SOPRunStatus.RUNNING:
                break

            # Nothing is suspended behind either of these prompts.
            if asked_permission is not None:
                inputs = await ask_permission(asked_permission)
                continue

            verdict = await ask_for_signoff(engine)
            if verdict is None:
                print("\nNothing left to answer; stopping.")
                break
            approval.answer = verdict

        print(f"\nWorkspace: {workspace.workdir}")


if __name__ == "__main__":
    asyncio.run(main())
