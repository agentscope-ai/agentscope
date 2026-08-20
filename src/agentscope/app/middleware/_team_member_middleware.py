# -*- coding: utf-8 -*-
"""The team member middleware that should be equipped with the member agents
within a team."""

import json
from typing import AsyncGenerator, Callable

from ..._utils._common import _json_loads_with_repair
from ...agent import Agent
from ...event import HintBlockEvent, ReplyEndEvent
from ...message import (
    HintBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)
from ...middleware import MiddlewareBase
from ...types import ReplyFinishedReason


class TeamMemberLoopMiddleware(MiddlewareBase):
    """The team member loop engineering middleware, that requires:

    1. The member should end its reply by calling `TeamSay` tool to report to
     the team leader.
    2. When exceeds the max iteration numbers, the agent is guided to send
    team leader a message to ask for permission to continue the operation.
    """

    def __init__(self, leader_name: str) -> None:
        """Initialize the middleware."""
        super().__init__()
        self._leader_name: str = leader_name

    def _last_tool_call_reports_to_leader(self, agent: "Agent") -> bool:
        """Whether this reply's final tool call successfully reports back."""
        for msg in reversed(agent.state.context):
            if (
                msg.id != agent.state.reply_id
                or msg.role != "assistant"
                or msg.name != agent.name
            ):
                continue

            blocks = msg.get_content_blocks()
            last_tool_call = next(
                (
                    block
                    for block in reversed(blocks)
                    if isinstance(block, ToolCallBlock)
                ),
                None,
            )
            if last_tool_call is None:
                continue

            # The last tool action must be a successful TeamSay addressed to
            # the leader or broadcast to the whole team.  A later non-TeamSay
            # tool therefore invalidates an earlier progress report.
            if last_tool_call.name != "TeamSay":
                return False
            kwargs = _json_loads_with_repair(last_tool_call.input)
            if kwargs.get("to") not in [None, self._leader_name]:
                return False

            result = next(
                (
                    block
                    for block in blocks
                    if isinstance(block, ToolResultBlock)
                    and block.id == last_tool_call.id
                ),
                None,
            )
            return (
                result is not None and result.state == ToolResultState.SUCCESS
            )

        return False

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Discard normal `ReplyEndEvent`s until `TeamSay` reports success."""

        async for evt in next_handler(**input_kwargs):
            if not isinstance(evt, ReplyEndEvent):
                yield evt
                continue

            # For the ReplyEndEvent
            instruction = None
            if self._last_tool_call_reports_to_leader(agent):
                # The report has been delivered successfully, so let the
                # original reply-end event escape the middleware chain.
                yield evt
                continue

            match evt.finished_reason:
                case ReplyFinishedReason.EXCEED_MAX_ITERS:
                    # Add instruction to guide agent to report to leader
                    instruction = (
                        "<system-reminder>You're now reach the max ReAct "
                        f"iteration numbers {agent.react_config.max_iters}. "
                        "Now you should call `TeamSay` to report to the "
                        "leader and ask for permission to continue."
                        "</system-reminder>"
                    )
                    # Reduce the current iter to allow the agent to call the
                    # `TeamSay` and avoid duplicated reply end event
                    agent.state.cur_iter = agent.react_config.max_iters - 1
                case ReplyFinishedReason.COMPLETED:
                    instruction = (
                        "<system-reminder>You MUST call the tool `TeamSay` "
                        "to report to the leader to finish your task."
                        "</system-reminder>"
                    )
                # TODO: When the subagent fails, the leader should be aware of
                #  of that.
                case ReplyFinishedReason.ERROR:
                    pass
                case ReplyFinishedReason.INTERRUPTED:
                    pass

            if instruction:
                # Inject the hint block into the context
                hint_block = HintBlock(
                    hint=instruction,
                    source=json.dumps(
                        {"label": "System", "sublabel": "Reminder"},
                    ),
                )
                agent.state.append_context(agent.name, [hint_block])
                yield HintBlockEvent(
                    reply_id=agent.state.reply_id,
                    block_id=hint_block.id,
                    source=hint_block.source,
                    hint=instruction,
                )
            else:
                # Interrupted/error endings cannot be continued by swallowing
                # their ReplyEndEvent.  Forward them unchanged.
                yield evt
