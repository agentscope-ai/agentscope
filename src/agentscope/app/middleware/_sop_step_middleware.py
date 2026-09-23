# -*- coding: utf-8 -*-
"""The middleware that holds a SOP step's agent to its submission."""

import json
from typing import AsyncGenerator, Callable

from ...agent import Agent
from ...event import HintBlockEvent, ReplyEndEvent
from ...message import HintBlock, ToolCallBlock, ToolResultBlock
from ...message import ToolResultState
from ...middleware import MiddlewareBase
from ...types import ErrorInfo, ErrorType, ReplyFinishedReason


class SOPStepSubmitMiddleware(MiddlewareBase):
    """Require a step's agent to end its turn through its submit tool.

    A SOP step's result is a row in the run state, written by
    ``SubmitHandover`` or ``SubmitVerdict``. An agent that simply stops
    talking has produced nothing the run can act on, so its reply-end is
    swallowed and it is told to submit; after ``max_nudges`` of those the
    reply is failed instead, which costs the step one attempt rather
    than holding the session forever.

    A session only ever carries one of the two tools, so this accepts
    either and names whichever the agent has when it nudges.
    """

    #: The submit tools, either of which ends a step's turn. Which one
    #: an agent actually has is decided in :func:`get_toolkit` from the
    #: half of the step it is playing, so this middleware accepts
    #: whichever is there rather than deriving that role a second time.
    TOOLS = ("SubmitHandover", "SubmitVerdict")

    def __init__(self, max_nudges: int = 3) -> None:
        """Initialize the middleware.

        Args:
            max_nudges (`int`, defaults to `3`):
                How many times one reply may be sent back before it is
                failed instead.
        """
        super().__init__()
        self._max_nudges = max_nudges

    def _submitted(self, agent: "Agent") -> bool:
        """Whether this reply's last tool call was a successful submit.

        A later tool call invalidates an earlier submission the same way
        it does for a team report: whatever the agent did last is what
        its turn amounts to.

        Args:
            agent (`Agent`):
                The replying agent, read for its context and the id of
                the reply in flight.

        Returns:
            `bool`:
                ``True`` when the reply ends on an accepted submission.
        """
        for msg in reversed(agent.state.context):
            if (
                msg.id != agent.state.reply_id
                or msg.role != "assistant"
                or msg.name != agent.name
            ):
                continue

            blocks = msg.get_content_blocks()
            last_call = next(
                (
                    block
                    for block in reversed(blocks)
                    if isinstance(block, ToolCallBlock)
                ),
                None,
            )
            if last_call is None:
                continue
            if last_call.name not in self.TOOLS:
                return False

            result = next(
                (
                    block
                    for block in blocks
                    if isinstance(block, ToolResultBlock)
                    and block.id == last_call.id
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
        """Discard reply-ends until the submit tool has been called.

        Args:
            agent (`Agent`):
                The replying agent. Its context receives the reminders,
                and its ``cur_iter`` is relaxed so a nudged reply can
                still act.
            input_kwargs (`dict`):
                The reply arguments, forwarded unchanged.
            next_handler (`Callable[..., AsyncGenerator]`):
                The rest of the middleware chain.

        Yields:
            `AgentEvent | Msg`:
                Everything the inner reply produces, except a reply-end
                that arrives with nothing submitted: that becomes a
                reminder, or an ``ERROR`` ending once the nudges run out.
        """
        nudges = 0

        async for event in next_handler(**input_kwargs):
            if not isinstance(event, ReplyEndEvent):
                yield event
                continue

            if self._submitted(agent):
                yield event
                continue

            if event.finished_reason not in (
                ReplyFinishedReason.COMPLETED,
                ReplyFinishedReason.EXCEED_MAX_ITERS,
            ):
                # An interrupted or already-failed ending cannot be
                # continued by swallowing it.
                yield event
                continue

            if nudges >= self._max_nudges:
                yield ReplyEndEvent(
                    session_id=event.session_id,
                    reply_id=event.reply_id,
                    finished_reason=ReplyFinishedReason.ERROR,
                    error=ErrorInfo(
                        type=ErrorType.INTERNAL,
                        message=(
                            f"{agent.name} ended {nudges} replies in a "
                            f"row without submitting anything to the "
                            f"procedure; giving up on this attempt."
                        ),
                    ),
                )
                continue

            nudges += 1
            equipped = {
                tool.name
                for group in agent.toolkit.tool_groups
                for tool in group.tools
            } & set(self.TOOLS)
            instruction = (
                f"<system-reminder>Your turn is not over until you call "
                f"`{sorted(equipped)[0] if equipped else self.TOOLS[0]}`. "
                f"Call it now.</system-reminder>"
            )
            # Free one iteration so the agent can actually make the call;
            # without it a reply that ended on its last iteration comes
            # straight back as EXCEED_MAX_ITERS with nothing in between.
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
