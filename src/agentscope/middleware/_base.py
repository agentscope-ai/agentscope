# -*- coding: utf-8 -*-
"""Base middleware class for AgentScope middleware system."""
from typing import AsyncGenerator, Awaitable, Callable, TYPE_CHECKING

from ..tool import ToolBase
from ..permission import PermissionEvaluation, PermissionRule

if TYPE_CHECKING:
    from ..agent import Agent
    from ..model import ChatResponse
    from ..message import ToolCallBlock


class MiddlewareBase:  # pylint: disable=unused-argument
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

    **Read-only Notification Hook** (no next_handler):
    - `on_permission_decision`: Observe a permission decision before the
      agent consumes it. Cannot modify, skip, or re-run the check.
    - `on_permission_confirmation`: Observe a user's approval or rejection
      before the agent consumes it.

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
                - inputs: Msg | list[Msg] | UserConfirmResultEvent |
                ExternalExecutionResultEvent | None — the unified inputs
                that trigger this reply (new message(s), a resumption
                event from a previous outside interaction, or None).
            next_handler: Callable that executes the next middleware or
             original method

        Yields:
            AgentEvent | Msg: Events from the reply process
        """
        raise RuntimeError(
            f"{type(self).__name__} does not implement on_reply",
        )
        yield  # pylint: disable=unreachable

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
        raise RuntimeError(
            f"{type(self).__name__} does not implement on_reasoning",
        )
        yield  # pylint: disable=unreachable

    async def on_acting(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Hook for intercepting the raw tool execution.

        This hook wraps **only** the ``toolkit.call_tool`` call — i.e. the
        pure I/O execution layer.  Permission checking, input validation, and
        context writes are handled by the agent **outside** this hook and are
        therefore not visible here.

        This separation makes it safe to offload the ``next_handler``
        coroutine to a background task: it will never mutate agent context
        on its own.

        .. note::
            Tools with ``is_state_injected=True`` receive the live
            ``agent.state`` object.  Offloading such tools to a background
            task may cause concurrent state mutations — guard against this
            in your middleware implementation.

        Args:
            agent (`Agent`):
                The Agent instance executing this middleware.
            input_kwargs (`dict`):
                Dictionary containing:

                - ``tool_call`` (``ToolCallBlock``): the tool call to execute.
                  By the time this hook is invoked the tool call has already
                  been validated and permitted.
            next_handler (`Callable[..., AsyncGenerator]`):
                Callable that executes the next middleware or
                ``_acting_impl``.

        Yields:
            `ToolChunk | ToolResponse`:
                Intermediate ``ToolChunk`` objects followed by a final
                ``ToolResponse`` produced by the tool.
        """
        raise RuntimeError(
            f"{type(self).__name__} does not implement on_acting",
        )
        yield  # pylint: disable=unreachable

    async def on_permission_decision(
        self,
        agent: "Agent",
        tool_call: "ToolCallBlock",
        tool: ToolBase,
        tool_input: dict,
        evaluation: PermissionEvaluation,
    ) -> None:
        """Observe a permission decision before the agent consumes it.

        Read-only notification hook, invoked after input validation and
        permission evaluation succeed, but **before** the agent updates
        tool-call state, emits ASK/DENY/ALLOW events, or calls
        :meth:`_acting`. This is the only observation point for DENY,
        ASK, and mode-suppressed decisions (BYPASS silencing a
        bypass-immune safety ASK, DONT_ASK converting an ASK to DENY) —
        :meth:`on_acting` only sees already-permitted executions.

        Contract:
        - **Read-only**: there is no ``next_handler``. Implementations
          must NOT replace, skip, or re-run the permission check, and
          must NOT mutate ``evaluation`` or ``tool_input`` in a way that
          affects the decision.
        - **Exceptions propagate** (fail-closed): an exception raised
          here aborts the tool call. Wrap in try/except for best-effort
          logging.

        Args:
            agent (`Agent`): The agent instance (provides ``name``,
                ``state.session_id``, ``state.reply_id``).
            tool_call (`ToolCallBlock`): The tool call block (provides
                ``id`` and the raw model-produced input).
            tool (`ToolBase`): The resolved tool instance.
            tool_input (`dict`): Schema-parsed and validated input.
                **May contain sensitive data** (shell commands, file
                contents, API tokens) — consumers are responsible for
                redaction / field whitelisting / truncation.
            evaluation (`PermissionEvaluation`): The structured
                permission evaluation, including any candidate decision
                suppressed by the active mode.
        """
        raise RuntimeError(
            f"{type(self).__name__} does not implement on_permission_decision",
        )

    async def on_permission_confirmation(
        self,
        agent: "Agent",
        tool_call: "ToolCallBlock",
        confirmed: bool,
        rules: list[PermissionRule],
    ) -> None:
        """Observe a user approval or rejection before Agent consumes it.

        This is a read-only notification hook with no ``next_handler``.
        It runs before Agent changes tool-call state, applies accepted rules,
        emits a denied result, or resumes execution. Exceptions propagate
        (fail-closed); catch sink failures inside the middleware to implement
        best-effort logging.

        Args:
            agent (`Agent`):
                The agent processing the confirmation.
            tool_call (`ToolCallBlock`):
                The tool call submitted with the confirmation, including
                any user-approved edits. Its input may contain sensitive
                data and must be redacted before logging.
            confirmed (`bool`):
                Whether the user approved the tool call.
            rules (`list[PermissionRule]`):
                Rules submitted with the confirmation. Rule content may be
                sensitive and must be redacted before logging.
        """
        raise RuntimeError(
            f"{type(self).__name__} does not implement "
            "on_permission_confirmation",
        )

    async def on_model_call(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[
            ...,
            Awaitable["ChatResponse" | AsyncGenerator["ChatResponse", None]],
        ],
    ) -> "ChatResponse" | AsyncGenerator["ChatResponse", None]:
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
        raise RuntimeError(
            f"{type(self).__name__} does not implement on_model_call",
        )

    async def on_compress_context(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[None]],
    ) -> None:
        """Onion hook for `compress_context` function in `Agent` class

        Args:
            agent (`Agent`):
                The Agent instance executing this middleware
            input_kwargs (`dict`):
                Dictionary containing:
                - context_config: ContextConfig | None
                - instructions: HintBlock | None
            next_handler (`Callable[..., Awaitable[None]]`):
                Callable that executes the next middleware or
                original method
        """
        raise RuntimeError(
            f"{type(self).__name__} does not implement on_compress_context",
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
        raise RuntimeError(
            f"{type(self).__name__} does not implement on_system_prompt",
        )

    async def list_tools(self) -> list[ToolBase]:
        """List available tools provided by this middleware. Optional to
        implement.

        Returns:
            `list[ToolBase]`:
                A list of tools provided by this middleware.
        """
        return []

    async def get_middleware_key(self) -> str:
        """Get the unique key for this middleware, used to save middleware
        states in `AgentState` instances.

        Optionally, middleware classes can override this method to
        provide their own unique key.
        """
        return self.__class__.__name__
