# -*- coding: utf-8 -*-
"""Middleware that keeps team workers from finishing without reporting."""
from typing import Any, AsyncGenerator, Callable

from .._tool import TeamSay
from ..._logging import logger
from ...agent import Agent
from ...event import (
    ExternalExecutionResultEvent,
    HintBlockEvent,
    UserConfirmResultEvent,
)
from ...message import AssistantMsg, HintBlock, Msg, ToolResultState
from ...middleware import MiddlewareBase
from ...tool import ToolResponse


_REMINDER = (
    "You have produced a private final answer, but the team leader is still "
    "waiting for your report. Call TeamSay with your final result before "
    "finishing. You may send it directly to the leader or broadcast it."
)


class TeamReportMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Require team workers to report their result to the leader.

    The middleware observes successful ``TeamSay`` executions via structured
    tool metadata. When a worker attempts to finish with plain assistant text
    before such a report, it injects a reminder hint and lets the same ReAct
    run continue, up to a small retry cap.
    """

    def __init__(self, max_report_reminders: int = 2) -> None:
        """Initialize the middleware.

        Args:
            max_report_reminders (`int`, defaults to `2`):
                Maximum number of silent final answers to suppress before
                allowing the worker to finish anyway.
        """
        self._max_report_reminders = max_report_reminders
        self._report_reminders = 0
        self._reported_to_leader = False

    async def on_reply(  # type: ignore[override]
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Reset report state when a fresh reply starts."""
        inputs = input_kwargs["inputs"]
        if not isinstance(
            inputs,
            (UserConfirmResultEvent, ExternalExecutionResultEvent),
        ):
            self._report_reminders = 0
            self._reported_to_leader = False

        async for item in next_handler(**input_kwargs):
            yield item

    async def on_acting(  # type: ignore[override]
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Record whether a ``TeamSay`` call reported to the leader."""
        tool_call = input_kwargs.get("tool_call")
        if tool_call is None:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        async for item in next_handler(**input_kwargs):
            if (
                tool_call.name == TeamSay.name
                and isinstance(item, ToolResponse)
                and item.state == ToolResultState.SUCCESS
            ):
                team_say = item.metadata.get("team_say", {})
                if team_say.get("reports_to_leader") is True:
                    self._reported_to_leader = True

            yield item

    async def on_reasoning(  # type: ignore[override]
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Intercept silent final answers and inject report reminders."""
        async for item in next_handler(**input_kwargs):
            if not isinstance(item, Msg):
                yield item
                continue

            if self._reported_to_leader:
                yield item
                continue

            if not self._can_remind(agent):
                logger.warning(
                    "Team worker %s finished without reporting to the "
                    "leader after %d reminder(s).",
                    agent.name,
                    self._report_reminders,
                )
                yield item
                continue

            hint = HintBlock(
                hint=_REMINDER,
                source="team_report_middleware",
            )
            # The event stream does not write hint blocks back to context; the
            # next model call needs this block in the worker's local history.
            self._append_hint(agent, hint)
            self._report_reminders += 1

            logger.info(
                "Team worker %s attempted to finish without TeamSay; "
                "injecting report reminder %d/%d.",
                agent.name,
                self._report_reminders,
                self._max_report_reminders,
            )

            yield HintBlockEvent(
                reply_id=agent.state.reply_id,
                block_id=hint.id,
                source=hint.source,
                hint=hint.hint,
            )

    def _can_remind(self, agent: Agent) -> bool:
        """Return whether another reminder can safely keep the run alive."""
        # A reminder consumes another ReAct iteration. On the final available
        # iteration we allow the answer through and log instead of forcing the
        # run into the max-iteration fallback.
        return (
            self._max_report_reminders > 0
            and self._report_reminders < self._max_report_reminders
            and agent.state.cur_iter < agent.react_config.max_iters - 1
        )

    @staticmethod
    def _append_hint(agent: Agent, hint: HintBlock) -> None:
        """Append a reminder hint to the worker's assistant context."""
        if agent.state.context:
            last_msg = agent.state.context[-1]
            if last_msg.role == "assistant" and last_msg.name == agent.name:
                last_msg.content.extend([hint])
                return

        agent.state.context.append(
            AssistantMsg(
                id=agent.state.reply_id,
                name=agent.name,
                content=[hint],
            ),
        )
