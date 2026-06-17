# -*- coding: utf-8 -*-
"""Budget control middleware for AgentScope agents."""
from typing import AsyncGenerator, Callable, TYPE_CHECKING

from ..event import ModelCallEndEvent, ReplyStartEvent, ReplyEndEvent
from ..message import AssistantMsg, HintBlock
from ..tool import ToolChoice
from ._base import MiddlewareBase

if TYPE_CHECKING:
    from ..agent import Agent

_DEFAULT_HINT_MESSAGE = (
    "<system-reminder>You have reached the maximum token budget set by the "
    "user. Now you MUST wrap up immediately and provide a final "
    "concluding response without invoking any tools."
    "</system-reminder>"
)


class ReplyBudgetControlMiddleware(MiddlewareBase):
    """Middleware that enforces a weighted token budget per reply.

    Tracks cumulative weighted token usage across all reasoning steps within
    a single reply. The weighted cost is computed as::

    cost = input_token_weight * input_tokens + output_token_weight * \
    output_tokens

    Once the accumulated cost reaches ``token_budget``, a hint message is
    injected into the agent's context before the next reasoning step, and
    ``tool_choice`` is forced to ``"none"`` so the agent wraps up without
    invoking any further tools.

    Budget state is stored in
    :attr:`~agentscope.agent.AgentState.middle_context`
    keyed by the middleware key, so it persists across human-in-the-loop (HITL)
    interruptions and resumptions. State is automatically cleaned up when the
    reply ends via a :class:`~agentscope.event.ReplyEndEvent`.

    .. note::
        The middleware is stateless on the instance itself — all runtime state
        lives in ``agent.state.middle_context``. This means the same middleware
        instance can safely be shared across multiple agents.

    Example::

        from agentscope.middleware import BudgetControlMiddleware

        agent = Agent(
            ...,
            middlewares=[
                BudgetControlMiddleware(
                    token_budget=10000,
                    input_token_weight=1.0,
                    output_token_weight=2.0,
                )
            ],
        )

    """

    def __init__(
        self,
        token_budget: float,
        input_token_weight: float = 1,
        output_token_weight: float = 1,
        hint_message: str = _DEFAULT_HINT_MESSAGE,
    ) -> None:
        """Initialize the budget control middleware.

        Args:
            token_budget (`float`):
                Maximum weighted token cost allowed per reply. The cost for
                each model call is computed as
                ``input_token_weight * input_tokens + output_token_weight *
                output_tokens``. Once the accumulated cost reaches this
                threshold, the agent is instructed to wrap up without calling
                any more tools.
            input_token_weight (`float`, optional):
                Multiplier applied to input tokens when computing the
                weighted cost. Defaults to ``1``.
            output_token_weight (`float`, optional):
                Multiplier applied to output tokens when computing the
                weighted cost. Defaults to ``1``. Set this higher than
                ``input_token_weight`` to reflect that output tokens are
                typically more expensive.
            hint_message (`str`, optional):
                The message injected into the agent's context when the budget
                is exceeded. Defaults to a built-in wrap-up prompt.
        """
        self.token_budget = token_budget
        self.input_token_weight = input_token_weight
        self.output_token_weight = output_token_weight

        self.hint_message = hint_message

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Manage per-reply budget state in ``agent.state.middle_context``.

        Initializes the weighted cost counter for the reply on
        :class:`~agentscope.event.ReplyStartEvent`, accumulates cost on each
        :class:`~agentscope.event.ModelCallEndEvent`, and removes the entry on
        :class:`~agentscope.event.ReplyEndEvent`.

        Args:
            agent (`Agent`):
                The agent instance executing this middleware.
            input_kwargs (`dict`):
                Reply input kwargs (passed through unchanged).
            next_handler (`Callable[..., AsyncGenerator]`):
                Callable that executes the next middleware or ``_reply``.

        Yields:
            Events from the reply process.
        """

        middleware_key = await self.get_middleware_key()

        async for event in next_handler(**input_kwargs):
            if isinstance(event, ReplyStartEvent):
                # Initialize the token counting number
                if middleware_key not in agent.state.middle_context:
                    agent.state.middle_context[middleware_key] = {}
                agent.state.middle_context[middleware_key][event.reply_id] = 0

            elif isinstance(event, ReplyEndEvent):
                # Clean up the token counting number
                agent.state.middle_context[middleware_key].pop(
                    event.reply_id,
                    None,
                )

            elif isinstance(event, ModelCallEndEvent):
                # Update the used tokens
                if middleware_key not in agent.state.middle_context:
                    agent.state.middle_context[middleware_key] = {}
                agent.state.middle_context[middleware_key][event.reply_id] += (
                    self.input_token_weight * event.input_tokens
                    + self.output_token_weight * event.output_tokens
                )

            yield event

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Intercept each reasoning step to enforce the token budget.

        Before forwarding to the next handler, reads the accumulated weighted
        cost for the current reply from ``agent.state.middle_context``. If the
        budget is exhausted, appends a
        :class:`~agentscope.message.HintBlock` to the last assistant message
        in context (or creates a new
        :class:`~agentscope.message.AssistantMsg`) and overrides
        ``tool_choice`` to ``ToolChoice(mode="none")``.

        Args:
            agent (`Agent`):
                The agent instance executing this middleware.
            input_kwargs (`dict`):
                Dictionary containing ``tool_choice`` and other reasoning
                kwargs forwarded to the next handler.
            next_handler (`Callable[..., AsyncGenerator]`):
                Callable that executes the next middleware or
                ``_reasoning_impl``.

        Yields:
            Events from the reasoning process.
        """
        reply_id = agent.state.reply_id
        middleware_key = await self.get_middleware_key()
        used = agent.state.middle_context.get(
            middleware_key,
            {},
        ).get(reply_id, 0)

        # Insert hint block if exceeded budget
        if used >= self.token_budget:
            hint_block = HintBlock(hint=self.hint_message)
            if (
                len(agent.state.context) > 0
                and agent.state.context[-1].role == "assistant"
                and agent.state.context[-1].name == agent.name
            ):
                agent.state.context[-1].content.append(hint_block)

            else:
                agent.state.context.append(
                    AssistantMsg(
                        id=agent.state.reply_id,
                        name=agent.name,
                        content=[hint_block],
                    ),
                )
            input_kwargs["tool_choice"] = ToolChoice(mode="none")

        async for event in next_handler(**input_kwargs):
            yield event
