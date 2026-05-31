# -*- coding: utf-8 -*-
"""Budget control middleware for AgentScope agents."""
from typing import AsyncGenerator, Callable, TYPE_CHECKING

from ..event import ModelCallEndEvent
from ..message import UserMsg
from ..tool import ToolChoice
from ._base import MiddlewareBase

if TYPE_CHECKING:
    from ..agent import Agent

_DEFAULT_HINT_MESSAGE = (
    "You have reached the maximum token budget. "
    "Please wrap up immediately and provide a final "
    "concluding response without invoking any tools."
)


class BudgetControlMiddleware(MiddlewareBase):
    """Middleware that enforces a maximum token budget per reply.

    Tracks cumulative token usage (input + output) across all reasoning
    steps within a single reply. Once the budget is reached, a hint message
    is injected into the agent's context before the next reasoning step,
    and ``tool_choice`` is forced to ``"none"`` so the agent wraps up
    without invoking any further tools.

    Example::

        from agentscope.middleware import BudgetControlMiddleware

        agent = Agent(
            ...,
            middlewares=[BudgetControlMiddleware(max_tokens=10000)],
        )
    """

    def __init__(
        self,
        max_tokens: int,
        hint_message: str = _DEFAULT_HINT_MESSAGE,
    ) -> None:
        """Initialize the budget control middleware.

        Args:
            max_tokens (`int`):
                Maximum total tokens (input + output) allowed per reply.
                Once this threshold is reached, the agent is instructed to
                wrap up without calling any more tools.
            hint_message (`str`, optional):
                The message injected into the agent's context when the
                budget is exceeded. Defaults to a built-in wrap-up prompt.
        """
        self.max_tokens = max_tokens
        self.hint_message = hint_message
        self._used_tokens: dict[str, int] = {}

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Intercept each reasoning step to enforce the token budget.

        Before forwarding to the next handler, checks accumulated token
        usage for the current reply. If the budget is exhausted, injects
        the hint message into context and overrides ``tool_choice`` to
        ``ToolChoice(mode="none")``.

        Args:
            agent (`Agent`):
                The agent instance executing this middleware.
            input_kwargs (`dict`):
                Dictionary containing ``tool_choice``.
            next_handler (`Callable[..., AsyncGenerator]`):
                Callable that executes the next middleware or
                ``_reasoning_impl``.

        Yields:
            Events from the reasoning process.
        """
        reply_id = agent.state.reply_id
        used = self._used_tokens.get(reply_id, 0)
        tool_choice = input_kwargs.get("tool_choice")

        if used >= self.max_tokens:
            agent.state.context.append(
                UserMsg(name="user", content=self.hint_message),
            )
            tool_choice = ToolChoice(mode="none")

        async for event in next_handler(tool_choice=tool_choice):
            if isinstance(event, ModelCallEndEvent):
                self._used_tokens[reply_id] = (
                    self._used_tokens.get(reply_id, 0)
                    + event.input_tokens
                    + event.output_tokens
                )
            yield event
