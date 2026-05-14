# -*- coding: utf-8 -*-
"""Base middleware class for AgentScope middleware system."""
from typing import AsyncGenerator, Awaitable, Callable, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import Agent
    from ..model import ChatResponse


class MiddlewareBase:
    """Base class for all middleware implementations.

    Middleware provides interception mechanisms at 5 key execution points
    in the Agent lifecycle:

    **Onion Pattern Hooks** (with before/after logic):
    - `on_reply`: Intercepts the entire reply process
    - `on_reasoning`: Intercepts the reasoning/model call phase
    - `on_acting`: Intercepts individual tool call execution
    - `on_model_call`: Intercepts the raw model API call

    **Transformer Pattern Hook** (sequential pipeline):
    - `on_system_prompt`: Transforms the system prompt string

    Each hook is optional - only implement the ones you need. The middleware
    system will automatically detect which hooks are implemented at runtime.

    Example:
        ```python
        class LoggingMiddleware(MiddlewareBase):
            async def on_reasoning(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[[], AsyncGenerator],
            ) -> AsyncGenerator:
                print(f"Before reasoning for agent {agent.name}")
                async for event in next_handler():
                    yield event
                print(f"After reasoning for agent {agent.name}")

        agent = Agent(
            ...
            middlewares=[LoggingMiddleware()],
            ...
        )
        ```
    """

    def is_implemented(self, hook_name: str) -> bool:
        """Check if a hook method is implemented in the subclass.

        Args:
            hook_name: Name of the hook method to check

        Returns:
            True if the hook is implemented (overridden), False otherwise
        """
        base_method = getattr(MiddlewareBase, hook_name, None)
        sub_method = getattr(type(self), hook_name, None)
        return base_method is not sub_method

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Hook for intercepting the reply process.

        Args:
            agent: The Agent instance executing this middleware
            input_kwargs: Dictionary containing:
                - msgs: Msg | list[Msg] | None
                - event: UserConfirmResultEvent |
                ExternalExecutionResultEvent | None
            next_handler: Callable that executes the next middleware or
             original method

        Yields:
            AgentEvent | Msg: Events from the reply process
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement on_reply",
        )

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Hook for intercepting the reasoning process.

        Args:
            agent: The Agent instance executing this middleware
            input_kwargs: Dictionary containing:
                - tool_choice: ToolChoice (default None)
            next_handler: Callable that executes the next middleware or
            original method

        Yields:
            Various events from the reasoning process
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement on_reasoning",
        )

    async def on_acting(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Hook for intercepting tool call execution.

        Args:
            agent: The Agent instance executing this middleware
            input_kwargs: Dictionary containing:
                - tool_call: ToolCallBlock
            next_handler: Callable that executes the next middleware or
            original method

        Yields:
            Events from the tool execution process
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement on_acting",
        )

    async def on_model_call(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[
            ...,
            Awaitable["ChatResponse" | AsyncGenerator["ChatResponse", None]],
        ],
    ) -> Union["ChatResponse", AsyncGenerator["ChatResponse", None]]:
        """Hook for intercepting the model API call.

        Args:
            agent: The Agent instance executing this middleware
            input_kwargs: Dictionary containing:
                - messages: list[Msg]
                - tools: list[dict]
                - tool_choice: ToolChoice
                - current_model: The model instance used for this call
            next_handler: Callable that executes the next middleware or
            original method

        Returns:
            ChatResponse or AsyncGenerator[ChatResponse, None]
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement on_model_call",
        )

    async def on_system_prompt(
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """Transform the system prompt string.

        This uses a transformer/pipeline pattern rather than onion pattern.
        Multiple middlewares are applied sequentially, each receiving the
        output of the previous one.

        Args:
            agent: The Agent instance executing this middleware
            current_prompt: The current system prompt string

        Returns:
            str: The transformed system prompt
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement " f"on_system_prompt",
        )
