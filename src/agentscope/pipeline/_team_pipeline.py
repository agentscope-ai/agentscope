# -*- coding: utf-8 -*-
"""The team pipeline class."""
import asyncio
from asyncio import Queue
from typing import Any, AsyncGenerator

from pydantic import BaseModel, ConfigDict

from .._utils._common import _json_loads_with_repair
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
    assignments to the same member run one after another in call order, a
    later one waiting until the earlier one finishes, even across a HITL
    pause. Members do not talk to each other; a member's result always
    returns to the leader.

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
        self._assign_tool = _TeamAssign(members)
        self._tool_registered = False

    async def reply_stream(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Reply to the given inputs and stream the events of every
        participant.

        Args:
            inputs (`Msg | list[Msg] | UserConfirmResultEvent | \
            UserInterruptEvent | ExternalExecutionResultEvent | None`, \
            optional):
                Messages go to the leader. A confirmation or external
                result is routed by ``reply_id`` to whichever participant is
                parked on it. An interrupt aborts every parked participant.

        Yields:
            `AgentEvent`:
                The events of the leader and the members it assigns to. The
                stream ends when every participant has either finished or
                parked on a HITL request.
        """
        if not self._tool_registered:
            await self.leader.toolkit.add_tool(self._assign_tool)
            self._tool_registered = True

        try:
            async for evt in self._reply(inputs):
                yield evt
        except asyncio.CancelledError:
            # A leader parked on assignments misses the cancellation, so
            # interrupt it and the parked members as a user interrupt does
            if self.leader.state.has_awaiting_tool_calls(self.leader.name):
                async for evt in self.reply_stream(
                    UserInterruptEvent(reply_id=self.leader.state.reply_id),
                ):
                    yield evt
            if self.leader.react_config.interruption_raise_cancelled_error:
                raise

    async def _reply(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Route the inputs and drive the leader and the members, see
        :meth:`reply_stream`."""
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
            # member finishes, so its reply becomes the tool result. The
            # first awaiting call is the parked one, the rest are queued
            results: list[ToolResultBlock] = []
            member = self._parked_member(inputs.reply_id)
            async for evt in self._run_assignments(
                self._assigning_calls(member),
                results,
                inputs,
            ):
                yield evt
            if not results:
                return
            inputs = ExternalExecutionResultEvent(
                reply_id=self.leader.state.reply_id,
                execution_results=results,
            )

        while True:
            assignments: list[ToolCallBlock] = []
            async for evt in self.leader.reply_stream(inputs):
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

    async def _run_assignments(
        self,
        tool_calls: list[ToolCallBlock],
        results: list[ToolResultBlock],
        resumed: UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run one round of assignments, concurrently across members and
        sequentially within one, appending the tool results of those that
        finish in call order. With ``resumed``, the first call is the parked
        one and continues with it."""
        groups: dict[str, list[ToolCallBlock]] = {}
        for tool_call in tool_calls:
            member = self._parse(tool_call)["member"]
            groups.setdefault(member, []).append(tool_call)

        sentinel = object()
        queue: Queue = Queue()
        round_results: list[ToolResultBlock] = []

        async def run_group(name: str, calls: list[ToolCallBlock]) -> None:
            """Run the assignments to one member one after another, turning
            each final reply into the tool result; once the member parks,
            the rest stay queued until it resumes."""
            member = self.members[name]
            for tool_call in calls:
                async for evt in member.agent.reply_stream(
                    resumed
                    if tool_call is tool_calls[0] and resumed
                    else UserMsg(
                        name="user",
                        content=self._parse(tool_call)["prompt"],
                    ),
                    yield_final_msg=True,
                ):
                    if not isinstance(evt, Msg):
                        await queue.put(evt)
                    elif evt.finished_reason is not None:
                        round_results.append(
                            ToolResultBlock(
                                id=tool_call.id,
                                name=_TeamAssign.name,
                                output=[
                                    _
                                    for _ in evt.content
                                    if isinstance(_, (TextBlock, DataBlock))
                                ]
                                or "The member finished without a reply.",
                                state=_RESULT_STATES[evt.finished_reason],
                            ),
                        )
                if self._is_parked(member):
                    break

        async def run_all() -> None:
            """Run every group and mark the end of the stream."""
            tasks = [
                asyncio.create_task(run_group(*group))
                for group in groups.items()
            ]
            try:
                await asyncio.gather(*tasks)
            finally:
                # gather propagates a member failure without stopping siblings.
                # Join them before ending the round, preserving the exception.
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await queue.put(sentinel)

        gather_task = asyncio.create_task(run_all())
        try:
            while (evt := await queue.get()) is not sentinel:
                yield evt
            await gather_task
        except asyncio.CancelledError:
            # The members close themselves as interrupted in their own tasks,
            # stream what they produced before passing the cancellation on
            gather_task.cancel()
            await asyncio.gather(gather_task, return_exceptions=True)
            while not queue.empty():
                if (evt := queue.get_nowait()) is not sentinel:
                    yield evt
            raise
        finally:
            gather_task.cancel()
            await asyncio.gather(gather_task, return_exceptions=True)
        # Results land in the leader's context in the order it called
        order = {_.id: i for i, _ in enumerate(tool_calls)}
        results.extend(sorted(round_results, key=lambda _: order[_.id]))

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

    def _parse(self, tool_call: ToolCallBlock) -> dict:
        """Parse an assignment's input the same way the leader validated
        it, so a repaired input parses here too."""
        return _json_loads_with_repair(
            tool_call.input,
            self._assign_tool.input_schema,
        )

    def _assigning_calls(self, member: TeamMember) -> list[ToolCallBlock]:
        """The leader's awaiting assignments to the member in call order,
        the first of which is the one the member is running."""
        return [
            _
            for _ in self.leader.state.get_awaiting_tool_calls(
                self.leader.name,
            )
            if _.name == _TeamAssign.name
            and self._parse(_)["member"] == member.agent.name
        ]
