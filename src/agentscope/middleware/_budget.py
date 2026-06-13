# -*- coding: utf-8 -*-
"""Budget control middleware for AgentScope agents."""
from typing import AsyncGenerator, Callable, TYPE_CHECKING

from ..event import ModelCallEndEvent, ReplyStartEvent
from ..message import AssistantMsg, HintBlock
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

    Token counters and the hint-injected flag are kept in per-reply
    dictionaries keyed by ``reply_id`` and are cleaned up automatically
    when the reply ends via ``on_reply``. The middleware is therefore
    stateless across replies — it does **not** accumulate tokens between
    user turns.

    .. note::
        This middleware does not support human-in-the-loop (HITL)
        interruption or resumption. A paused reply resumes with whatever
        token count was tracked before the pause; it cannot account for
        budget state that is meaningful across a HITL round-trip.

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
        self._hint_injected: dict[str, bool] = {}

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Wrap the full reply to clean up per-reply state on completion.

        Captures the ``reply_id`` from
        :class:`~agentscope.event.ReplyStartEvent` and removes the
        corresponding entries from ``_used_tokens`` and
        ``_hint_injected`` once the reply completes — even on error.

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
        reply_id = None
        try:
            async for event in next_handler(**input_kwargs):
                if isinstance(event, ReplyStartEvent) and reply_id is None:
                    reply_id = event.reply_id
                yield event
        finally:
            cleanup_id = reply_id or agent.state.reply_id
            self._used_tokens.pop(cleanup_id, None)
            self._hint_injected.pop(cleanup_id, None)

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Intercept each reasoning step to enforce the token budget.

        Before forwarding to the next handler, checks accumulated token
        usage for the current reply. If the budget is exhausted and the
        hint has not yet been injected, appends a
        :class:`~agentscope.message.HintBlock` to the last assistant
        message in context (or creates a new
        :class:`~agentscope.message.AssistantMsg`) and overrides
        ``tool_choice`` to ``ToolChoice(mode="none")``.

        Args:
            agent (`Agent`):
                The agent instance executing this middleware.
            input_kwargs (`dict`):
                Dictionary containing ``tool_choice`` and other
                reasoning kwargs forwarded to the next handler.
            next_handler (`Callable[..., AsyncGenerator]`):
                Callable that executes the next middleware or
                ``_reasoning_impl``.

        Yields:
            Events from the reasoning process.
        """
        reply_id = agent.state.reply_id
        used = self._used_tokens.get(reply_id, 0)

        if used >= self.max_tokens:
            if not self._hint_injected.get(reply_id, False):
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
                self._hint_injected[reply_id] = True
            input_kwargs["tool_choice"] = ToolChoice(mode="none")

        async for event in next_handler(**input_kwargs):
            if isinstance(event, ModelCallEndEvent):
                self._used_tokens[reply_id] = (
                    self._used_tokens.get(reply_id, 0)
                    + event.input_tokens
                    + event.output_tokens
                )
            yield event
