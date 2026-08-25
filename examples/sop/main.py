# -*- coding: utf-8 -*-
"""Handle a customer complaint by the book: establish, propose, reply.

The procedure is three milestones, and each one is a checkpoint somebody
actually cares about — is the story straight, is the offer within policy,
is the letter safe to send. How any of them gets done is the agent's
business.

Two properties are worth watching for.

**A gate can check against the same records the agent used.** Step one is
judged by a model that is handed ``orders.json`` and ``shipments.json``
itself, so "the facts check out" means they were checked, not that the
write-up read plausibly.

**Waiting costs nothing.** A supervisor signs off step two, and its
verifier says so by answering nothing at all. The stream ends, this
program blocks on ``input()`` with no agent suspended behind it, and the
run picks up when the answer arrives.

``data/`` stands in for the systems a support agent would really query —
an order service, a courier's API, the policy wiki. The SOP does not know
or care where the facts come from; it only says that step one must hand
over an account that survives checking.

Run with::

    export DASHSCOPE_API_KEY=sk-...
    python main.py
    python main.py --complaint "订单 A-1051 到现在还没动静"
"""
import argparse
import asyncio
import os
from typing import AsyncGenerator

from agentscope.agent import Agent
from agentscope.console import ConsoleRenderer
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.message import TextBlock, UserMsg
from agentscope.model import ChatModelBase, DashScopeChatModel
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
from agentscope.tool import Toolkit
from agentscope.workspace import LocalWorkspace

DEFAULT_COMPLAINT = (
    "订单 A-1043，你们说好三天到，我等了两个多星期还没收到货，"
    "物流一直不动。我要求全额退款。"
)


async def judge(model: ChatModelBase, prompt: str) -> tuple[bool, str]:
    """Ask a model for a verdict, and read it back as pass plus reason.

    The verdict is the first word so it survives a model that cannot
    resist adding a paragraph.

    A model built with ``stream=True`` answers with an async generator
    rather than one reply, so both shapes are handled — a judge has no
    use for streaming, but nothing stops you handing one in.
    """
    reply = await model([UserMsg("judge", prompt)])
    if isinstance(reply, AsyncGenerator):
        chunk = None
        async for chunk in reply:
            pass
        reply = chunk

    text = "".join(
        block.text
        for block in (reply.content if reply else [])
        if block.type == "text"
    ).strip()
    passed = text.upper().startswith("PASS")
    reason = text.split("\n", 1)[0][5:].lstrip(" :：-") or text
    return passed, ("" if passed else reason)


class FactsMatchRecords(VerifierBase):
    """Checks an account of what happened against the records themselves.

    The point of handing the judge the raw files is that it can catch a
    claim nobody could have made from them — a date that is not in the
    tracking, an amount that is not on the order. A judge without the
    records can only tell you the write-up reads well.

    Like every verifier here, it takes what it needs at construction: the
    engine never learns what a workspace is.
    """

    def __init__(
        self,
        model: ChatModelBase,
        workspace: LocalWorkspace,
        files: list[str],
    ) -> None:
        """Remember the judge, and which records are the truth.

        Paths are resolved against the workspace root. The backend's own
        working directory is wherever the process happens to be, which is
        not the same thing and is a reliable way to read nothing.
        """
        self._model = model
        self._backend = workspace.get_backend()
        self._files = [
            self._backend.join_path(workspace.workdir, "data", name)
            for name in files
        ]

    async def verify(self, sop, run, step, step_run) -> VerificationRecord:
        """Compare every claim in the submission with the records."""
        records = []
        for path in self._files:
            body = (await self._backend.read_file(path)).decode()
            records.append(f"### {os.path.basename(path)}\n{body}")

        passed, reason = await judge(
            self._model,
            "You are auditing a support agent's account of what happened.\n\n"
            "## The records (the only source of truth)\n"
            + "\n\n".join(records)
            + "\n\n## The account\n"
            + step_run.submission
            + "\n\n## Your job\n"
            "Reply PASS if every factual claim above is supported by the "
            "records. Otherwise reply FAIL followed by the specific claims "
            "that are wrong or unsupported, so the agent can go and fix "
            "them. Judge only the facts, not the writing.",
        )
        return VerificationRecord(
            passed=passed,
            message=reason,
            verified_by="records-audit",
        )


class SupervisorApproval(VerifierBase):
    """Waits for a supervisor, without waiting.

    Answering ``None`` is how a verifier says "not yet". The step stays
    in ``VERIFYING``, the engine lets go of the stream, and the run is
    asked again once :attr:`answer` has been filled in — a second later
    or a week.
    """

    def __init__(self) -> None:
        """Start with no answer."""
        self.answer: VerificationRecord | None = None

    async def verify(self, sop, run, step, step_run):
        """Hand over an answer if one has arrived, otherwise nothing."""
        answer, self.answer = self.answer, None
        return answer


class ReplyIsSafeToSend(VerifierBase):
    """Checks a draft reply against the offer that was approved.

    The failure this exists to catch is a warm, helpful letter that
    quietly promises more than the supervisor signed off on.
    """

    def __init__(self, model: ChatModelBase) -> None:
        """Remember the judge."""
        self._model = model

    async def verify(self, sop, run, step, step_run) -> VerificationRecord:
        """Compare the draft with the approved offer."""
        approved = next(
            (
                run.steps[s.id].submission
                for s in sop.steps
                if s.id == "propose"
            ),
            "",
        )
        passed, reason = await judge(
            self._model,
            "You are the last check before a reply goes to a customer.\n\n"
            "## What the supervisor approved\n" + approved
            + "\n\n## The draft reply\n" + step_run.submission
            + "\n\n## Your job\n"
            "Reply PASS if the draft offers exactly what was approved, "
            "promises nothing beyond it (no extra refunds, no delivery "
            "dates, no goodwill nobody agreed to), and is written as an "
            "apology with concrete next steps. Otherwise reply FAIL "
            "followed by what to change.",
        )
        return VerificationRecord(
            passed=passed,
            message=reason,
            verified_by="reply-check",
        )


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

    def model(stream: bool = True) -> DashScopeChatModel:
        return DashScopeChatModel(
            credential=DashScopeCredential(api_key=api_key),
            model=model_name,
            stream=stream,
        )

    tools = await workspace.list_tools()
    data = os.path.join(workspace.workdir, "data")

    support = Agent(
        name="support",
        system_prompt=(
            "You work in customer support. You establish what actually "
            f"happened by reading the records in {data}, and you never "
            "state anything they do not show."
        ),
        model=model(),
        toolkit=Toolkit(tools=tools),
        offloader=workspace,
    )
    policy = Agent(
        name="policy",
        system_prompt=(
            f"You apply the compensation policy in {data}/policy.md "
            "literally. You do not invent goodwill, and you say which "
            "clause each part of your proposal comes from."
        ),
        model=model(),
        toolkit=Toolkit(tools=tools),
        offloader=workspace,
    )
    # No tools at all: whoever writes to the customer works from the
    # approved offer, not from the order system.
    writer = Agent(
        name="writer",
        system_prompt=(
            "You write to customers in Chinese: apologetic, concrete, "
            "short. You have no access to any system — everything you "
            "know comes from what you were handed."
        ),
        model=model(),
    )

    # A verdict is one short answer; there is nothing to stream.
    judge_model = model(stream=False)

    return SOP(
        name="客户投诉处理",
        description="Establish the facts, propose redress, reply.",
        steps=[
            SOPStep(
                id="establish",
                subject="核实事实",
                description=(
                    f"Read {data}/orders.json and {data}/shipments.json "
                    "and work out what happened to this customer's order. "
                    "Submit an account: the order id, what was promised, "
                    "what the tracking actually shows, and how many days "
                    "late it now is. Every claim must be traceable to the "
                    "records."
                ),
                agent=support,
                verifier=FactsMatchRecords(
                    judge_model,
                    workspace,
                    ["orders.json", "shipments.json"],
                ),
            ),
            SOPStep(
                id="propose",
                subject="拟补偿方案",
                description=(
                    f"Read {data}/policy.md and propose what this customer "
                    "should be offered, given the facts you were handed. "
                    "Submit the offer with the clause it comes from, and "
                    "say plainly if it needs a supervisor."
                ),
                agent=policy,
                blocked_by=["establish"],
                verifier=SupervisorApproval(),
            ),
            SOPStep(
                id="reply",
                subject="写回复客户",
                description=(
                    "Draft the reply to the customer in Chinese. Offer "
                    "exactly what was approved and nothing more."
                ),
                agent=writer,
                blocked_by=["propose"],
                verifier=ReplyIsSafeToSend(judge_model),
            ),
        ],
    )


def show(event: object, renderer: ConsoleRenderer) -> None:
    """Print SOP events plainly and let the renderer handle the rest."""
    if isinstance(event, StepStateEvent):
        line = f"\n== {event.subject} - {event.state}"
        if event.message:
            line += f" - {event.message}"
        print(line)
    elif isinstance(event, RunSettledEvent):
        print(f"\n== run {event.status.value} {event.reason}".rstrip())
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


async def ask_supervisor(engine: SOPEngine) -> VerificationRecord | None:
    """Show the proposal and read a supervisor's decision.

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

    print("\n" + "-" * 60)
    print(f"{waiting.subject} needs your approval:\n")
    print(engine.run.steps[waiting.id].submission)
    print("-" * 60)

    verdict = await asyncio.to_thread(input, "Approve? [y/N] ")
    if verdict.strip().lower() in ("y", "yes"):
        return VerificationRecord(passed=True, verified_by="supervisor")
    reason = await asyncio.to_thread(input, "What should change? ")
    return VerificationRecord(
        passed=False,
        message=reason.strip() or "Not approved as it stands.",
        verified_by="supervisor",
    )


async def main() -> None:
    """Run the procedure, pausing whenever a person is needed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.7-max")
    parser.add_argument("--complaint", default=DEFAULT_COMPLAINT)
    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set the DASHSCOPE_API_KEY environment variable before "
            "running this demo.",
        )

    here = os.path.dirname(os.path.abspath(__file__))
    async with LocalWorkspace(workdir=here) as workspace:
        sop = await build_sop(workspace, args.model, api_key)
        engine = SOPEngine(sop)
        renderer = ConsoleRenderer()
        approval: SupervisorApproval = sop.steps[1].verifier

        inputs: list | UserConfirmResultEvent | None = [
            TextBlock(text=args.complaint),
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

            verdict = await ask_supervisor(engine)
            if verdict is None:
                print("\nNothing left to answer; stopping.")
                break
            approval.answer = verdict

        reply = engine.run.steps["reply"].submission
        if reply:
            print("\n" + "=" * 60)
            print("给客户的回复：\n")
            print(reply)

        # Close the models' HTTP clients before the loop tears down.
        for step in sop.steps:
            client = getattr(step.agent.model, "client", None)
            if client is not None:
                await client.close()


if __name__ == "__main__":
    asyncio.run(main())
