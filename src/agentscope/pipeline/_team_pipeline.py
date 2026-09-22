# -*- coding: utf-8 -*-
"""The team pipeline class."""
import asyncio
import json
from asyncio import Queue
from typing import Any, AsyncGenerator

from pydantic import BaseModel, ConfigDict

from ..agent import Agent
from ..event import (
    AgentEvent,
    ExternalExecutionResultEvent,
    ReplyEndEvent,
    RequireExternalExecutionEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message import (
    DataBlock,
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from ..permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ..tool import ToolBase
from ..types import ReplyFinishedReason


class TeamMember(BaseModel):
    """A member of the team: an existing agent plus the description the
    leader reads to decide when to assign a task to it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent: Agent
    """The member agent. Its name is how the leader refers to it, so it
    must be unique within the team."""
    description: str
    """What the member is good at, when to assign to it and what it
    returns. Presented to the leader in the tool description."""


class _TeamAssign(ToolBase):
    """The external tool the leader calls to assign a task to a member.
    The pipeline executes it by running the member, so it never runs by
    itself."""

    name: str = "TeamAssign"
    is_external_tool: bool = True
    is_concurrency_safe: bool = True
    is_read_only: bool = False

    def __init__(self, members: list[TeamMember]) -> None:
        """Build the tool from the members, listing them in the description
        and offering their names as the choices of ``member``."""
        super().__init__()
        self.description = (
            "Assign a task to a team member and get its reply back as the "
            "result. The members are:\n"
            + "\n".join(f"- {_.agent.name}: {_.description}" for _ in members)
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "member": {
                    "type": "string",
                    "enum": [_.agent.name for _ in members],
                    "description": "The name of the member to assign to.",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "The complete task for the member. It cannot see "
                        "your context, so include everything it needs."
                    ),
                },
            },
            "required": ["member", "prompt"],
        }

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Assigning itself is always allowed; the member's own tools go
        through their own permission checks."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Assigning a task to a team member is always allowed.",
        )


_RESULT_STATES = {
    ReplyFinishedReason.COMPLETED: ToolResultState.SUCCESS,
    ReplyFinishedReason.EXCEED_MAX_ITERS: ToolResultState.SUCCESS,
    ReplyFinishedReason.INTERRUPTED: ToolResultState.INTERRUPTED,
    ReplyFinishedReason.ERROR: ToolResultState.ERROR,
}


class TeamPipeline:
    """A leader agent that assigns tasks to team members through tool calls.

    The leader gets one external tool, ``TeamAssign``, that names a member
    and a task. When the leader calls it, the pipeline runs that member in
    its own context and feeds its final reply back as the tool result, so
    the leader only ever sees the summary, never the member's intermediate
    steps. Members assigned in the same round run concurrently, except that
    assignments to the same member run one after another. Members do not
    talk to each other; a member's result always returns to the leader.

    HITL works per participant: a member that needs user confirmation parks
    like any agent, its request is streamed out unchanged, and the result
    sent back to the pipeline is routed to it by ``reply_id``. Meanwhile
    the leader stays parked on the assignment and resumes once the member
    finishes.
    """

    def __init__(
        self,
        leader: Agent,
        members: list[TeamMember],
        reset_members: bool = True,
    ) -> None:
        """Initialize the team pipeline.

        Args:
            leader (`Agent`):
                The leader agent that assigns tasks.
            members (`list[TeamMember]`):
                The members the leader can assign tasks to.
            reset_members (`bool`, optional):
                Whether to clear every member's context after the leader's
                reply ends. Within one reply the leader can follow up with
                a member; across replies each member starts afresh.
                Defaults to `True`.
        """
        names = [_.agent.name for _ in members]
        if len(set(names)) != len(names) or leader.name in names:
            raise ValueError(
                "Member names must be unique and differ from the leader's, "
                f"got {names!r} with leader {leader.name!r}.",
            )
        self.leader = leader
        self.members = {_.agent.name: _ for _ in members}
        self.reset_members = reset_members
        self._tool_registered = False

    async def reply_stream(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Reply to the given inputs and stream the events of every
        participant.

        Args:
            inputs (`Msg | list[Msg] | UserConfirmResultEvent | \
            UserInterruptEvent | ExternalExecutionResultEvent | None`, \
            optional):
                Messages go to the leader. A confirmation or external
                result is routed by ``reply_id`` to whichever participant is
                parked on it. An interrupt aborts every parked participant.
            yield_final_msg (`bool`, defaults to `False`):
                If yield the leader's final reply message.

        Yields:
            `AgentEvent | Msg`:
                The events of the leader and the members it assigns to. The
                stream ends when every participant has either finished or
                parked on a HITL request.
        """
        if not self._tool_registered:
            await self.leader.toolkit.add_tool(
                _TeamAssign(list(self.members.values())),
            )
            self._tool_registered = True

        if isinstance(inputs, UserInterruptEvent):
            for member in self._parked_members():
                async for evt in member.agent.reply_stream(
                    UserInterruptEvent(reply_id=member.agent.state.reply_id),
                ):
                    yield evt
            inputs = UserInterruptEvent(reply_id=self.leader.state.reply_id)

        elif (
            isinstance(
                inputs,
                (UserConfirmResultEvent, ExternalExecutionResultEvent),
            )
            and inputs.reply_id != self.leader.state.reply_id
        ):
            # A parked member's turn; the leader only continues once the
            # member finishes, so its reply becomes the tool result
            results: list[ToolResultBlock] = []
            member = self._parked_member(inputs.reply_id)
            async for evt in self._run_member(member, inputs, results):
                yield evt
            if not results:
                return
            inputs = ExternalExecutionResultEvent(
                reply_id=self.leader.state.reply_id,
                execution_results=results,
            )

        while True:
            assignments: list[ToolCallBlock] = []
            async for evt in self.leader.reply_stream(
                inputs,
                yield_final_msg=yield_final_msg,
            ):
                # Assignments are executed by the pipeline itself, so their
                # require events are not the caller's business
                if isinstance(evt, RequireExternalExecutionEvent) and all(
                    _.name == _TeamAssign.name for _ in evt.tool_calls
                ):
                    assignments.extend(evt.tool_calls)
                    continue
                yield evt
                if isinstance(evt, ReplyEndEvent) and self.reset_members:
                    for member in self.members.values():
                        member.agent.state.context.clear()
                        member.agent.state.summary = ""

            if not assignments:
                return

            results = []
            async for evt in self._run_assignments(assignments, results):
                yield evt

            # Feed what has finished; with a member parked on HITL the
            # leader stays parked too and the stream ends here
            inputs = ExternalExecutionResultEvent(
                reply_id=self.leader.state.reply_id,
                execution_results=results,
            )
            if len(results) < len(assignments):
                if results:
                    async for evt in self.leader.reply_stream(inputs):
                        yield evt
                return

    async def reply(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> Msg:
        """Reply to the given inputs, consuming all streamed events.

        Args:
            inputs (`Msg | list[Msg] | UserConfirmResultEvent | \
            UserInterruptEvent | ExternalExecutionResultEvent | None`, \
            optional):
                The inputs, see :meth:`reply_stream`.

        Returns:
            `Msg`:
                The leader's final reply message.
        """
        final_msg: Msg | None = None
        async for evt_or_msg in self.reply_stream(
            inputs, yield_final_msg=True
        ):
            if isinstance(evt_or_msg, Msg):
                final_msg = evt_or_msg
        if final_msg is None:
            raise RuntimeError("Agent did not produce a final message.")
        return final_msg

    async def _run_assignments(
        self,
        tool_calls: list[ToolCallBlock],
        results: list[ToolResultBlock],
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run one round of assignments, concurrently across members and
        sequentially within one, collecting the tool results of those that
        finish."""
        groups: dict[str, list[ToolCallBlock]] = {}
        for tool_call in tool_calls:
            member = json.loads(tool_call.input)["member"]
            groups.setdefault(member, []).append(tool_call)

        sentinel = object()
        queue: Queue = Queue()

        async def run_group(name: str, calls: list[ToolCallBlock]) -> None:
            """Run the assignments to one member one after another."""
            member = self.members[name]
            for tool_call in calls:
                if self._is_parked(member):
                    results.append(
                        self._to_tool_result(
                            tool_call.id,
                            f"The member {name!r} is waiting for the user on "
                            "a previous task, try again later.",
                            ToolResultState.ERROR,
                        ),
                    )
                    continue
                prompt = json.loads(tool_call.input)["prompt"]
                async for evt in self._run_member(
                    member,
                    UserMsg(name="user", content=prompt),
                    results,
                    tool_call.id,
                ):
                    await queue.put(evt)

        async def run_all() -> None:
            """Run every group and mark the end of the stream."""
            try:
                await asyncio.gather(*[run_group(*_) for _ in groups.items()])
            finally:
                await queue.put(sentinel)

        gather_task = asyncio.create_task(run_all())
        try:
            while (evt := await queue.get()) is not sentinel:
                yield evt
            await gather_task
        finally:
            gather_task.cancel()
        # Results land in the leader's context in the order it called
        order = {_.id: i for i, _ in enumerate(tool_calls)}
        results.sort(key=lambda _: order[_.id])

    async def _run_member(
        self,
        member: TeamMember,
        inputs: Msg | UserConfirmResultEvent | ExternalExecutionResultEvent,
        results: list[ToolResultBlock],
        tool_call_id: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run one member reply, streaming its events and turning its final
        message into the tool result of the assigning call. Nothing is
        appended when the member parks on a HITL request."""
        tool_call_id = tool_call_id or self._assigning_call(member).id
        async for evt in member.agent.reply_stream(
            inputs,
            yield_final_msg=True,
        ):
            if not isinstance(evt, Msg):
                yield evt
            elif evt.finished_reason is not None:
                results.append(
                    self._to_tool_result(
                        tool_call_id,
                        [
                            _
                            for _ in evt.content
                            if isinstance(_, (TextBlock, DataBlock))
                        ],
                        _RESULT_STATES[evt.finished_reason],
                    ),
                )

    @staticmethod
    def _to_tool_result(
        tool_call_id: str,
        output: str | list[TextBlock | DataBlock],
        state: ToolResultState,
    ) -> ToolResultBlock:
        """Build the tool result the leader receives for an assignment."""
        return ToolResultBlock(
            id=tool_call_id,
            name=_TeamAssign.name,
            output=output or "The member finished without a reply.",
            state=state,
        )

    @staticmethod
    def _is_parked(member: TeamMember) -> bool:
        """Whether the member is waiting on a HITL request."""
        return member.agent.state.has_awaiting_tool_calls(member.agent.name)

    def _parked_members(self) -> list[TeamMember]:
        """The members waiting on a HITL request."""
        return [_ for _ in self.members.values() if self._is_parked(_)]

    def _parked_member(self, reply_id: str) -> TeamMember:
        """The member parked on the reply the given result belongs to."""
        for member in self._parked_members():
            if member.agent.state.reply_id == reply_id:
                return member
        raise ValueError(
            f"No participant is waiting on the reply {reply_id!r}.",
        )

    def _assigning_call(self, member: TeamMember) -> ToolCallBlock:
        """The leader's submitted tool call that assigns to the member.
        Assignments to one member run one after another, so at most one is
        in flight."""
        for tool_call in self.leader.state.get_awaiting_tool_calls(
            self.leader.name,
        ):
            if (
                tool_call.name == _TeamAssign.name
                and tool_call.state == ToolCallState.SUBMITTED
                and json.loads(tool_call.input)["member"] == member.agent.name
            ):
                return tool_call
        raise RuntimeError(
            f"The leader is not assigning to {member.agent.name!r}.",
        )
