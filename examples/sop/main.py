# -*- coding: utf-8 -*-
"""Handle a customer complaint by the book: establish, propose, reply.

Three milestones, each a checkpoint somebody actually cares about — is
the story straight, is the offer within policy, is the letter safe to
send. How any of them gets done is the agents' business.

Three things are worth watching for.

**A verifier is just another agent.** Step one is judged by an auditor
that can read ``orders.json`` and ``shipments.json`` itself, so "the
facts check out" means they were checked, not that the write-up read
plausibly. The engine cannot tell an auditor from a supervisor from
nothing at all — it reads verdicts, never verifiers.

**Waiting costs nothing.** A supervisor signs off step two, and its
verifier says so by asking and stopping. The stream ends, this program
blocks on ``input()`` with no agent suspended behind it, and the run
picks up when the answer arrives.

**A refusal comes back as a critique.** Whatever a verifier says on the
way to ``passed=False`` is handed to the executor verbatim on its next
attempt, along with which attempt it is.

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
from agentscope.message import (
    AssistantMsg,
    Msg,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.model import DashScopeChatModel
from agentscope.sop import (
    SOP,
    SOPEngine,
    SOPPhase,
    SOPStep,
)
from agentscope.tool import Toolkit
from agentscope.types import ReplyFinishedReason
from agentscope.workspace import LocalWorkspace
from agentscope._utils._common import _generate_id

DEFAULT_COMPLAINT = "订单 A-1043，说好三天到，两个多星期没收到，物流不动。我要求全额退款。"


class SupervisorApproval:
    """A verifier that is a person, and does not hold anything open.

    It satisfies the same protocol an :class:`~agentscope.agent.Agent`
    does, so the step cannot tell the difference: asked with nothing to
    go on, it posts its question and stops; asked again with an answer,
    it turns that answer into a verdict.
    """

    name = "supervisor"

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
        approved = answer.lower() in ("y", "yes", "是", "同意", "批准")
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
    model_name: str,
    api_key: str,
) -> SOP:
    """Assemble the procedure — executors, verifiers and all.

    At this layer a SOP is code: a step holds the agent that runs it and
    the one that judges it, both already built.
    """
    tools = await workspace.list_tools()
    data = os.path.join(workspace.workdir, "data")

    def model(stream: bool = True) -> DashScopeChatModel:
        return DashScopeChatModel(
            credential=DashScopeCredential(api_key=api_key),
            model=model_name,
            stream=stream,
        )

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
    auditor = Agent(
        name="auditor",
        system_prompt=(
            "You audit a support agent's account of what happened. The "
            f"records in {data} are the only source of truth — read them "
            "and check every factual claim against them. Judge the facts, "
            "not the writing. When you refuse, name the specific claims "
            "that are wrong or unsupported so they can be fixed."
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
    safety = Agent(
        name="safety",
        system_prompt=(
            "You are the last check before a reply goes to a customer. "
            "Refuse a draft that offers anything beyond what was "
            "approved — no extra refunds, no delivery dates, no goodwill "
            "nobody agreed to — or that is not written as an apology "
            "with concrete next steps."
        ),
        model=model(),
    )

    return SOP(
        name="客户投诉处理",
        description="Establish the facts, propose redress, reply.",
        steps=[
            SOPStep(
                subject="核实事实",
                description=(
                    f"Read {data}/orders.json and {data}/shipments.json "
                    "and work out what happened to this customer's order. "
                    "Hand over an account: the order id, what was "
                    "promised, what the tracking actually shows, and how "
                    "many days late it now is. Every claim must be "
                    "traceable to the records."
                ),
                executor=support,
                verifier=auditor,
                step_id="establish",
            ),
            SOPStep(
                subject="拟补偿方案",
                description=(
                    f"Read {data}/policy.md and propose what this "
                    "customer should be offered, given the facts you were "
                    "handed. Hand over the offer with the clause it comes "
                    "from."
                ),
                executor=policy,
                verifier=SupervisorApproval(),
                step_id="propose",
            ),
            SOPStep(
                subject="写回复客户",
                description=(
                    "Draft the reply to the customer in Chinese. Offer "
                    "exactly what was approved and nothing more."
                ),
                executor=writer,
                verifier=safety,
                step_id="reply",
            ),
        ],
        sop_id="complaint",
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

        inputs: Msg | UserConfirmResultEvent | ExternalExecutionResultEvent = (
            UserMsg(name="user", content=args.complaint)
        )
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

        reply = "".join(
            block.text
            for block in (engine.state.steps["reply"].submission or [])
            if block.type == "text"
        )
        if reply:
            print("\n" + "=" * 60)
            print("给客户的回复：\n")
            print(reply)


if __name__ == "__main__":
    asyncio.run(main())
