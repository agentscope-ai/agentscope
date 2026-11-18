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
from ...types import ErrorInfo, ErrorType, ReplyFinishedReason


class TeamMemberLoopMiddleware(MiddlewareBase):
    """The team member loop engineering middleware, that requires:

    1. The member should end its reply by calling `TeamSay` tool to report to
     the team leader.
    2. When exceeds the max iteration numbers, the agent is guided to send
    team leader a message to ask for permission to continue the operation.

    A member that keeps ending without reporting is nudged at most
    ``max_nudges`` times; after that the reply is released as an
    ``ERROR`` so it terminates and the failure travels the normal
    error path instead of looping under the session lock forever.
    """

    def __init__(self, leader_name: str, max_nudges: int = 3) -> None:
        """Initialize the middleware.

        Args:
            leader_name (`str`):
                The name of the team leader, i.e. the only valid
                ``TeamSay(to=...)`` target that counts as a report.
            max_nudges (`int`, defaults to 3):
                How many times one reply may be forced to continue
                before it is failed instead.
        """
        super().__init__()
        self._leader_name: str = leader_name
        self._max_nudges: int = max_nudges

    def _last_tool_call_reports_to_leader(self, agent: "Agent") -> bool:
        """Whether this reply's final tool call successfully reports back.

        Args:
            agent (`Agent`):
                The replying member agent, read for its context and
                the id of the reply in flight.

        Returns:
            `bool`:
                ``True`` when the reply's last tool call is a
                ``TeamSay`` addressed to the leader (or broadcast) that
                returned ``SUCCESS``.
        """
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
            try:
                kwargs = _json_loads_with_repair(last_tool_call.input)
            except Exception:  # pylint: disable=broad-except
                # Unparsable arguments (e.g. a truncated stream) cannot
                # have produced a successful report.
                return False
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
        """Discard normal `ReplyEndEvent`s until `TeamSay` reports success.

        Args:
            agent (`Agent`):
                The replying member agent. Its context receives the
                reminder hints, and its ``cur_iter`` is relaxed so a
                nudged reply can still act.
            input_kwargs (`dict`):
                The reply arguments, forwarded to ``next_handler``
                unchanged.
            next_handler (`Callable[..., AsyncGenerator]`):
                The rest of the middleware chain.

        Yields:
            `AgentEvent | Msg`:
                Everything the inner reply produces, except a
                ``ReplyEndEvent`` that arrives without a successful
                report: that one is replaced by a reminder
                ``HintBlockEvent`` (forcing another round) or, once the
                nudge budget is spent, by an ``ERROR`` reply-end event.
        """
        nudges = 0

        async for evt in next_handler(**input_kwargs):
            if not isinstance(evt, ReplyEndEvent):
                yield evt
                continue

            # For the ReplyEndEvent
            if self._last_tool_call_reports_to_leader(agent):
                # The report has been delivered successfully, so let the
                # original reply-end event escape the middleware chain.
                yield evt
                continue

            if evt.finished_reason not in (
                ReplyFinishedReason.COMPLETED,
                ReplyFinishedReason.EXCEED_MAX_ITERS,
            ):
                # Interrupted / already-failed endings cannot be continued
                # by swallowing their ReplyEndEvent.  Forward unchanged.
                # TODO: When the subagent fails, the leader should be aware
                #  of that.
                yield evt
                continue

            if nudges >= self._max_nudges:
                # Out of patience: end the reply as an error so it stops
                # holding the session, and let the error path report it.
                yield ReplyEndEvent(
                    session_id=evt.session_id,
                    reply_id=evt.reply_id,
                    finished_reason=ReplyFinishedReason.ERROR,
                    error=ErrorInfo(
                        type=ErrorType.INTERNAL,
                        message=(
                            f"{agent.name} ended {nudges} replies in a row "
                            f"without reporting to {self._leader_name} via "
                            f"TeamSay; giving up on this turn."
                        ),
                    ),
                )
                continue

            nudges += 1
            if evt.finished_reason == ReplyFinishedReason.EXCEED_MAX_ITERS:
                instruction = (
                    "<system-reminder>You have reached the maximum number "
                    f"of ReAct iterations ({agent.react_config.max_iters}). "
                    "Call `TeamSay` now to report to the leader and ask for "
                    "permission to continue.</system-reminder>"
                )
            else:
                instruction = (
                    "<system-reminder>You MUST call the tool `TeamSay` "
                    "to report to the leader to finish your task."
                    "</system-reminder>"
                )

            # Free one iteration so the agent can actually make the
            # TeamSay call. Both endings need this: a COMPLETED reply on
            # the very last iteration would otherwise come straight back
            # as EXCEED_MAX_ITERS with no reasoning in between, which the
            # agent rejects as a swallow-without-progress loop.
            agent.state.cur_iter = min(
                agent.state.cur_iter,
                agent.react_config.max_iters - 1,
            )

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
