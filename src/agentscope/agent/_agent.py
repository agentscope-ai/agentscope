# -*- coding: utf-8 -*-
"""The unified agent class in AgentScope library."""
import asyncio
import collections
import inspect
import re

from asyncio import Queue
from copy import deepcopy
from datetime import datetime
from typing import (
    Any,
    AsyncGenerator,
    Sequence,
    List,
    TYPE_CHECKING,
    Type,
)

import jsonschema
from pydantic import BaseModel

from ._config import ContextConfig, ReActConfig, ModelConfig, InjectionConfig
from ..state import AgentState
from ..state._state import ReplyContext
from ._utils import _ToolCallBatch, Acting, Exit, Reasoning, _resolve_timezone
from .._logging import logger
from .._utils._common import (
    _generate_id,
    _json_loads_with_repair,
    _execute_async_or_sync_func,
)
from ..event import (
    AgentEvent,
    ModelCallEndEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    RequireUserConfirmEvent,
    RequireExternalExecutionEvent,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    DataBlockStartEvent,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    ExceedMaxItersEvent,
    ReplyFinishedReason,
    UserInterruptEvent,
    HintBlockEvent,
)
from ..exception import AgentOrientedException
from ..model import (
    ChatResponse,
    ChatUsage,
    ChatModelBase,
    FinishedReason,
)
from ..message import (
    Msg,
    AssistantMsg,
    SystemMsg,
    UserMsg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    DataBlock,
    Base64Source,
    URLSource,
    ToolCallState,
    ToolResultState,
    Usage,
    HintBlock,
)
from ..tool import (
    Toolkit,
    ToolChunk,
    ToolChoice,
    ToolResponse,
)
from ..permission import (
    PermissionBehavior,
    PermissionEngine,
    PermissionDecision,
    PermissionRule,
)
from ._structured_output_tool import _GenerateStructuredOutput
from ..workspace import Offloader, WorkspaceBase

if TYPE_CHECKING:
    from ..middleware import MiddlewareBase
else:
    MiddlewareBase = Any


class Agent:
    """The agent class."""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        model: ChatModelBase,
        toolkit: Toolkit | None = None,
        middlewares: list[MiddlewareBase] | None = None,
        state: AgentState | None = None,
        offloader: Offloader | None = None,
        # The agent configurations
        model_config: ModelConfig | None = None,
        context_config: ContextConfig | None = None,
        react_config: ReActConfig | None = None,
        injection_config: InjectionConfig | None = None,
    ) -> None:
        """Initialize the agent class in AgentScope.

        Args:
            name (`str`):
                The agent identifier.
            system_prompt (`str`):
                The agent's system prompt. Additional instructions may be
                appended to it dynamically during operation.
            model (`ChatModelBase`):
                The chat model/llm used for this agent.
            toolkit (`Toolkit | None`, optional):
                The toolkit used for registering tools, MCPs and skills as the
                sole source.
            middlewares (`list[MiddlewareBase] | None`, optional):
                Middlewares applied to the agent to modify its behavior
                without altering its source code. Supported hook points
                include: reply, reasoning, acting, model call, and system
                prompt retrieval.
            state (`AgentState | None`, optional):
                The agent state. A new state will be created if not provided.
            offloader (`Offloader | None`, optional):
                The context offloader. If provided, the compressed context and
                tool result will be offloaded.
            model_config (`ModelConfig | None`, optional):
                The additional chat model configuration including fallback
                model and retries.
            context_config (`ContextConfig | None`, optional):
                The context config for context compression and tool result
                compression.
            react_config (`ReActConfig | None`, optional):
                The config for the reasoning-acting loop.
            injection_config (`InjectionConfig | None`, optional):
                The runtime state injection config, which controls how the
                time, (plan) tasks and context usage are injected into the
                context to help the agent better reason and act.
        """
        self.name = name
        self._system_prompt = system_prompt
        self.model = model
        self.state = state or AgentState()

        self.model_config = model_config or ModelConfig()
        self.context_config = context_config or ContextConfig()
        self.react_config = react_config or ReActConfig()
        self.injection_config = injection_config or InjectionConfig()
        self._validate_configs()

        # The permission engine
        self._engine = PermissionEngine(self.state.permission_context)

        # The offloader/workspace
        self.offloader = offloader

        # ====================================================================
        # The Tool-related logics
        # ====================================================================
        self.toolkit = toolkit or Toolkit()

        # ====================================================================
        # The Middleware-related attributes
        # ====================================================================
        # Filter middlewares by implemented hooks (only once)
        middlewares = middlewares or []
        self._reply_middlewares = [
            _ for _ in middlewares if _.is_implemented("on_reply")
        ]
        self._reasoning_middlewares = [
            _ for _ in middlewares if _.is_implemented("on_reasoning")
        ]
        self._acting_middlewares = [
            _ for _ in middlewares if _.is_implemented("on_acting")
        ]
        self._model_call_middlewares = [
            _ for _ in middlewares if _.is_implemented("on_model_call")
        ]
        self._system_prompt_middlewares = [
            _ for _ in middlewares if _.is_implemented("on_system_prompt")
        ]
        self._compress_context_middlewares = [
            _ for _ in middlewares if _.is_implemented("on_compress_context")
        ]

    def _validate_configs(self) -> None:
        """Validate the config combinations that a single config class cannot
        check by itself.

        Raises:
            `ValueError`:
                If the reserved/buffer ratios don't leave room ahead of the
                context compression threshold.
        """
        if (
            self.context_config.reserve_ratio
            >= self.context_config.trigger_ratio
        ):
            raise ValueError(
                "The 'reserve_ratio' of the context config must be smaller "
                "than its 'trigger_ratio', got "
                f"{self.context_config.reserve_ratio} and "
                f"{self.context_config.trigger_ratio}.",
            )

        if (
            self.injection_config.inject_runtime_state
            and self.injection_config.context_buffer_ratio
            >= self.context_config.trigger_ratio
        ):
            raise ValueError(
                "The 'context_buffer_ratio' of the injection config must be "
                "smaller than the 'trigger_ratio' of the context config, so "
                "that the context length is injected before the compression, "
                f"got {self.injection_config.context_buffer_ratio} and "
                f"{self.context_config.trigger_ratio}.",
            )

    # =======================================================================
    # Agent public methods
    # =======================================================================

    async def reply_stream(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None = None,
        structured_schema: Type[BaseModel] | None = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Reply to the given inputs and stream agent events.

        Args:
            inputs (`Msg | list[Msg] | UserConfirmResultEvent | \
            UserInterruptEvent | ExternalExecutionResultEvent | None`, \
            optional):
                The inputs that trigger this reply. See :meth:`reply` for
                the full list of accepted variants.
            structured_schema (`Type[BaseModel] | None`, optional):
                The Pydantic model class that the reply's structured output
                must conform to. See :meth:`reply` for details.
            yield_final_msg (`bool`, defaults to `False`):
                If yield the final reply message. When requiring structured
                output, use this option to get the final message, and access
                it via the `structured_output` attribute.

        Yields:
            `AgentEvent | Msg`:
                Streamed events produced during the reply.

        .. note:: If requiring outside interaction for multiple tool calls
            and only receive partial confirmation or execution results, the
            agent won't re-send the requiring events for the unconfirmed
            or unexecuted tool calls.
        """
        async for chunk in self._reply(
            inputs=inputs,
            structured_schema=structured_schema,
        ):
            if isinstance(chunk, Msg) and not yield_final_msg:
                continue
            yield chunk

    async def reply(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None = None,
        structured_schema: Type[BaseModel] | None = None,
    ) -> Msg:
        """Reply to the given inputs, consuming all streamed events.

        Args:
            inputs (`Msg | list[Msg] | UserConfirmResultEvent | \
            UserInterruptEvent | ExternalExecutionResultEvent | None`, \
            optional):
                The inputs that trigger this reply. It can be:

                - a single `Msg` or a list of `Msg` objects to start a new
                  reply,
                - a `UserConfirmResultEvent` or
                  `ExternalExecutionResultEvent` to continue from the
                  outside interaction required by the previous reply,
                - a `UserInterruptEvent` to abort a parked reply — the
                  agent closes all pending tool calls with an interrupted
                  tool result and ends the reply without entering the
                  reasoning-acting loop,
                - `None` if there is nothing new to feed in (e.g. just
                  continue from the current state).
            structured_schema (`Type[BaseModel] | None`, optional):
                The Pydantic model class that the reply's structured output
                must conform to, with the validated result carried on the
                final message's ``structured_output`` attribute as a dict.

        Returns:
            `Msg`:
                A final reply message.
        """
        final_msg: Msg | None = None
        async for evt_or_msg in self._reply(
            inputs=inputs,
            structured_schema=structured_schema,
        ):
            if isinstance(evt_or_msg, Msg):
                final_msg = evt_or_msg
        if final_msg is None:
            raise RuntimeError("Agent did not produce a final message.")
        return final_msg

    async def observe(self, msgs: Msg | list[Msg] | None = None) -> None:
        """Receive external observation message(s) and save them into
        context."""
        await self._handle_incoming_messages(msgs)

    async def compress_context(
        self,
        context_config: ContextConfig | None = None,
        instructions: HintBlock | None = None,
    ) -> None:
        """Compress the agent's context if the token count exceeds the
        threshold.

        Args:
            context_config (`ContextConfig | None`, optional):
                If provided, compress the context with the given context
                config. Otherwise, use the default context config in the
                agent.
            instructions (`HintBlock | None`, optional):
                Optional hints or instructions injected into the compression
                context to guide the summarization behavior.
        """
        if not self._compress_context_middlewares:
            await self._compress_context_impl(
                context_config=context_config,
                instructions=instructions,
            )
        else:

            async def execute_chain(
                index: int = 0,
                context_config: ContextConfig | None = context_config,
                instructions: HintBlock | None = instructions,
            ) -> None:
                """Execute the compress_context middleware chain."""
                if index >= len(self._compress_context_middlewares):
                    await self._compress_context_impl(
                        context_config=context_config,
                        instructions=instructions,
                    )
                else:
                    mw = self._compress_context_middlewares[index]
                    input_kwargs = {
                        "context_config": context_config,
                        "instructions": instructions,
                    }

                    async def next_handler(**kwargs: Any) -> None:
                        await execute_chain(
                            index + 1,
                            **{**input_kwargs, **kwargs},
                        )

                    await mw.on_compress_context(
                        agent=self,
                        input_kwargs=input_kwargs,
                        next_handler=next_handler,
                    )

            await execute_chain()

    async def _compress_context_impl(
        self,
        context_config: ContextConfig | None = None,
        instructions: HintBlock | None = None,
    ) -> None:
        """Compress the agent's context if the token count exceeds the
        threshold.

        Args:
            context_config (`ContextConfig | None`, optional):
                If provided, compress the context with the given context
                config. Otherwise, use the default context config in the
                agent.
            instructions (`HintBlock | None`, optional):
                Optional hints or instructions injected into the compression
                context to guide the summarization behavior.
        """
        cfg: ContextConfig = context_config or self.context_config

        # Count the current tokens
        kwargs = await self._prepare_model_input()
        estimated_tokens = await self.model.count_tokens(**kwargs)

        # Skip if no compression is needed
        threshold = cfg.trigger_ratio * self.model.context_size
        if estimated_tokens < threshold:
            return

        logger.info(
            "[AGENT %s]: Current token count %d exceeds the threshold %d, "
            "activating compression.",
            self.name,
            int(estimated_tokens),
            int(threshold),
        )

        if len(self.state.context) == 0:
            # The system prompt and the summary (if exists) exceeds the
            # threshold, which cannot be compressed, raise the error to the
            # developer!
            suffix = ""
            if self.state.summary:
                suffix = "and the compression summary "
            raise RuntimeError(
                f"The system prompt {suffix}exceed(s) the compression "
                f"threshold ({threshold} tokens), cannot be compressed.",
            )

        # Split the context into the ones to be compressed, and the others to
        # be reserved
        tools = kwargs.get("tools", [])
        (
            msgs_to_compress,
            msgs_to_reserve,
        ) = await self._split_context_for_compression(
            cfg.reserve_ratio * self.model.context_size,
            tools,
        )

        if len(msgs_to_compress) == 0:
            # The reserve ratio is too large so that although it exceeds the
            # trigger threshold, the context to be compressed is empty
            # Fallback by lowering the reserve ratio to compress more context.
            logger.warning(
                "The reserve ratio %.2f is too large to compress any context."
                "Lower the reserve ratio to 0 as a fallback.",
                cfg.reserve_ratio,
            )
            (
                msgs_to_compress,
                msgs_to_reserve,
            ) = await self._split_context_for_compression(
                0 * self.model.context_size,
                tools,
            )

            # The msgs to be compressed cannot be empty here, unless the
            # system prompt and summary (if any) already exceed the context
            # length, which we have handled before.

        # Prepare the messages to compress
        msgs_system = [
            SystemMsg(
                name="system",
                content=await self._get_system_prompt(),
            ),
        ]
        if self.state.summary:
            msgs_system.append(UserMsg("user", self.state.summary))

        instruction_msgs: list[Msg] = []
        if instructions is not None:
            instruction_msgs.append(
                AssistantMsg(
                    name=self.name,
                    content=[instructions],
                ),
            )

        messages = (
            msgs_system
            + msgs_to_compress
            + instruction_msgs
            + [
                UserMsg(name="user", content=cfg.compression_prompt),
            ]
        )

        # The compression prompt may exceed the context length, here we mark
        # the overflow by a bool flag
        compression_tool_schema = [
            {
                "type": "function",
                "function": {
                    "name": "generate_structured_output",
                    "description": "Call this function to generate "
                    "structured output required by "
                    "the user.",
                    "parameters": cfg.summary_schema,
                },
            },
        ]
        context_overflow = False
        estimated_compression_tokens = await self.model.count_tokens(
            messages,
            compression_tool_schema,
        )
        if estimated_compression_tokens > self.model.context_size:
            logger.warning(
                "The current context length exceeds the model's context "
                "length (%d tokens), the compression maybe failed due to "
                "insufficient reserved context for compression.",
                self.model.context_size,
            )
            context_overflow = True

        # Compress the messages
        try:
            res = await self.model.generate_structured_output(
                messages=messages,
                structured_model=cfg.summary_schema,
            )

        except Exception as e:
            if context_overflow:
                logger.warning(
                    "Failed to compress context, which may be caused by "
                    "insufficient reserved context for compression. "
                    "Trying to compress by removing the oldest context.",
                )
                for i in range(1, len(msgs_to_compress) + 1):
                    messages = (
                        msgs_system
                        + msgs_to_compress[i:]
                        + instruction_msgs
                        + [
                            UserMsg(
                                name="user",
                                content=cfg.compression_prompt,
                            ),
                        ]
                    )
                    estimated_compression_tokens = (
                        await self.model.count_tokens(
                            messages,
                            compression_tool_schema,
                        )
                    )
                    # Considering trigger_ratio <= 0.9, at least reserve 10%
                    # tokens for compression response
                    if (
                        estimated_compression_tokens
                        < self.model.context_size * cfg.trigger_ratio
                    ):
                        break

                res = await self.model.generate_structured_output(
                    messages=messages,
                    structured_model=cfg.summary_schema,
                )

            else:
                raise e from None

        if res.finished_reason == FinishedReason.INTERRUPTED:
            logger.warning(
                "The context compression was interrupted and skipped. ",
            )
            raise asyncio.CancelledError()

        # Update the summary
        async def _apply_change() -> None:
            """Apply the context change with interruption protection."""
            new_summary = cfg.summary_template.format(**res.content)
            if self.offloader:
                path = await self.offloader.offload_context(
                    self.state.session_id,
                    msgs=msgs_to_compress,
                )
                new_summary += (
                    f"\n<system-reminder>The compressed context is offloaded "
                    f"to '{path}', you can refer to it when needed."
                    f"</system-reminder>"
                )

            # Protected from interruption
            await self._clear_unreserved_read_cache(msgs_to_reserve)

            # Update the context
            self.state.summary = new_summary
            self.state.context = msgs_to_reserve

            logger.info(
                "[AGENT %s]: The context compression finished.",
                self.name,
            )

        apply_task = asyncio.create_task(_apply_change())
        try:
            await asyncio.shield(apply_task)
        except asyncio.CancelledError:
            await apply_task
            raise

    # ======================================================================
    # Agent core methods, including _reply, _reasoning, _acting, etc.
    # ======================================================================

    async def _reply(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None = None,
        structured_schema: Type[BaseModel] | None = None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Reply entry point (maybe wrapped by middleware)."""
        if not self._reply_middlewares:
            async for item in self._reply_impl(
                inputs=inputs,
                structured_schema=structured_schema,
            ):
                yield item
        else:

            async def execute_chain(
                index: int = 0,
                inputs: Msg
                | list[Msg]
                | UserConfirmResultEvent
                | UserInterruptEvent
                | ExternalExecutionResultEvent
                | None = inputs,
                structured_schema: Type[BaseModel] | None = structured_schema,
            ) -> AsyncGenerator[AgentEvent | Msg, None]:
                if index >= len(self._reply_middlewares):
                    async for item in self._reply_impl(
                        inputs=inputs,
                        structured_schema=structured_schema,
                    ):
                        yield item
                else:
                    mw = self._reply_middlewares[index]
                    input_kwargs = {
                        "inputs": inputs,
                        "structured_schema": structured_schema,
                    }

                    async def next_handler(
                        **kwargs: Any,
                    ) -> AsyncGenerator[AgentEvent | Msg, None]:
                        async for item in execute_chain(
                            index + 1,
                            **{**input_kwargs, **kwargs},
                        ):
                            yield item

                    async for item in mw.on_reply(
                        agent=self,
                        input_kwargs=input_kwargs,
                        next_handler=next_handler,
                    ):
                        yield item

            async for item in execute_chain():
                yield item

    async def _close_unfinished_tool_calls(
        self,
    ) -> AsyncGenerator[
        ToolResultStartEvent | ToolResultTextDeltaEvent | ToolResultEndEvent,
        None,
    ]:
        """Close the unfinished tool calls on interruption, so the next
        input will be handled normally."""
        if not self.state.context:
            return

        last_msg = self.state.context[-1]
        if last_msg.role != "assistant" or last_msg.name != self.name:
            return

        # Searching for tool calls that requires user confirmation or external
        # execution without tool results
        awaiting_tool_calls: dict = {}
        for index, block in enumerate(last_msg.content):
            if isinstance(block, ToolCallBlock):
                awaiting_tool_calls[block.id] = index
            elif isinstance(block, ToolResultBlock):
                awaiting_tool_calls.pop(block.id, None)

        interruption_message = (
            "<system-reminder>The tool call has been interrupted by "
            "the user.</system-reminder>"
        )

        for index in awaiting_tool_calls.values():
            call_block = last_msg.content[index]
            assert isinstance(call_block, ToolCallBlock)

            # An ALLOWED call was already running, so its START was already
            # emitted — skip it here (checked before flipping to FINISHED).
            if call_block.state != ToolCallState.ALLOWED:
                yield ToolResultStartEvent(
                    reply_id=self.state.reply_id,
                    tool_call_id=last_msg.content[index].id,
                    tool_call_name=last_msg.content[index].name,
                )

            call_block.state = ToolCallState.FINISHED
            yield ToolResultTextDeltaEvent(
                reply_id=self.state.reply_id,
                tool_call_id=last_msg.content[index].id,
                delta=interruption_message,
            )
            yield ToolResultEndEvent(
                reply_id=self.state.reply_id,
                tool_call_id=last_msg.content[index].id,
                state=ToolResultState.INTERRUPTED,
            )
            last_msg.content.append(
                ToolResultBlock(
                    id=last_msg.content[index].id,
                    name=last_msg.content[index].name,
                    output=interruption_message,
                    state=ToolResultState.INTERRUPTED,
                ),
            )

    async def _reply_impl(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None = None,
        structured_schema: Type[BaseModel] | None = None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Core reply logic."""

        end_event: ReplyEndEvent | None = None
        try:
            # Dispatch the unified inputs by type into the legacy local
            # variables
            event: (
                UserConfirmResultEvent
                | UserInterruptEvent
                | ExternalExecutionResultEvent
                | None
            )
            msgs: Msg | list[Msg] | None
            if isinstance(
                inputs,
                (
                    UserConfirmResultEvent,
                    UserInterruptEvent,
                    ExternalExecutionResultEvent,
                ),
            ):
                event = inputs
                msgs = None
            else:
                event = None
                msgs = inputs

            if event and structured_schema:
                logger.warning(
                    "The given structured_schema is ignored when "
                    "resuming with a HITL event; the parked reply's "
                    "schema is used instead.",
                )

            # Parked-interrupt short-circuit: only signal an INTERRUPTED
            # end when there is actual HITL work to close; otherwise the
            # session is effectively idle and the call is a silent no-op.
            # ``finally`` reuses the CancelledError cleanup path when
            # ``end_event`` is set — no reasoning-acting loop either way.
            if isinstance(inputs, UserInterruptEvent):
                if self.state.has_awaiting_tool_calls(self.name):
                    end_event = ReplyEndEvent(
                        session_id=self.state.session_id,
                        reply_id=self.state.reply_id,
                        finished_reason=ReplyFinishedReason.INTERRUPTED,
                    )
                return

            # ===================================================================
            # Step 1: Checking agent input:
            #  - if incoming event and agent is waiting for an event
            #  - if event is None and agent is not waiting for an event
            # ===================================================================
            is_awaiting = await self._check_incoming_event(event)

            # ===================================================================
            # Step 2: Handling agent event if applicable
            #  - yield tool result events for the denied tool calls, or
            #  - update the reply state as a new reply process
            # ===================================================================
            if is_awaiting:
                async for evt in self._handle_incoming_event(event):
                    yield evt
            else:
                await self._handle_incoming_messages(msgs)
                # Update the context with the incoming message and state
                self.state.reply_context = ReplyContext(
                    reply_id=_generate_id(),
                    cur_iter=0,
                    structured_schema=structured_schema,
                    structured_output=None,
                )

                yield ReplyStartEvent(
                    session_id=self.state.session_id,
                    reply_id=self.state.reply_id,
                    name=self.name,
                )

            # Update the structured output tool for new requirements or
            #  from the previous reply
            await self.toolkit.remove_tool(_GenerateStructuredOutput.name)
            if self.state.reply_context.structured_schema:
                await self.toolkit.add_tool(
                    _GenerateStructuredOutput(
                        schema=self.state.reply_context.structured_schema,
                    ),
                )

            # =================================================================
            # Step 3: Enter the reasoning-acting loop until reaching max_iters
            #  or no more tool calls to execute
            # =================================================================
            final_msg: Msg | None = None
            while True:
                # =============================================================
                # Step 3.1: Decide the next action based on the current state
                # =============================================================
                next_action = self._next_action(final_msg)

                match next_action:
                    case Exit(exit_msg=exit_msg, exit_events=exit_events):
                        for exit_event in exit_events or []:
                            yield exit_event
                        if exit_msg:
                            yield exit_msg
                        return

                    case Reasoning(hint=hint, tool_choice=tool_choice):
                        final_msg = None
                        if hint:
                            self.state.append_context(self.name, [hint])

                        # Compressed the memory if needed before reasoning
                        await self.compress_context()

                        # Inject runtime state if needed before reasoning
                        async for evt in self._inject_runtime_state():
                            yield evt

                        # Perform reasoning
                        interrupted = False
                        async for evt in self._reasoning(
                            tool_choice=tool_choice,
                        ):
                            if isinstance(evt, Msg):
                                # Candidate final message; ``_next_action``
                                # decides whether it ends the reply
                                final_msg = evt
                                continue

                            if isinstance(evt, ModelCallEndEvent):
                                interrupted = (
                                    evt.finished_reason
                                    == FinishedReason.INTERRUPTED
                                )

                            yield evt

                        if interrupted:
                            end_event = ReplyEndEvent(
                                session_id=self.state.session_id,
                                reply_id=self.state.reply_id,
                                finished_reason=(
                                    ReplyFinishedReason.INTERRUPTED
                                ),
                            )
                            return

                    case Acting(tool_calls=tool_calls):
                        for batch in await self._batch_tool_calls(tool_calls):
                            if batch.type == "sequential":
                                evt_generator = (
                                    self._execute_sequential_tool_calls(
                                        batch.tool_calls,
                                    )
                                )

                            elif batch.type == "concurrent":
                                evt_generator = (
                                    self._execute_concurrent_tool_calls(
                                        batch.tool_calls,
                                    )
                                )

                            else:
                                raise ValueError(
                                    f"Invalid batch type: {batch.type}",
                                )

                            break_execution_for_hitl = False
                            break_execution_for_interruption = False
                            async for evt in evt_generator:
                                yield evt
                                if isinstance(
                                    evt,
                                    (
                                        RequireUserConfirmEvent,
                                        RequireExternalExecutionEvent,
                                    ),
                                ):
                                    break_execution_for_hitl = True

                                elif (
                                    isinstance(evt, ToolResultEndEvent)
                                    and evt.state
                                    == ToolResultState.INTERRUPTED
                                ):
                                    # Handle the interruption event
                                    break_execution_for_interruption = True

                            if break_execution_for_interruption:
                                end_event = ReplyEndEvent(
                                    session_id=self.state.session_id,
                                    reply_id=self.state.reply_id,
                                    finished_reason=(
                                        ReplyFinishedReason.INTERRUPTED
                                    ),
                                )
                                return

                            if break_execution_for_hitl:
                                break

                        if break_execution_for_hitl:
                            # Stop executing the next batches, and go back to
                            # ``_next_action``, which parks the reply on the
                            # awaiting tool calls. The unfinished round isn't
                            # counted into ``cur_iter``
                            continue

                # Update iteration count after each round of reasoning-acting
                self.state.cur_iter += 1

        except asyncio.CancelledError:
            # Handle the CancelledError within the _reply_impl for the
            # agent middlewares
            end_event = ReplyEndEvent(
                session_id=self.state.session_id,
                reply_id=self.state.reply_id,
                finished_reason=ReplyFinishedReason.INTERRUPTED,
            )

            if self.react_config.interruption_raise_cancelled_error:
                raise

        finally:
            if end_event is not None:
                interrupted_end = (
                    end_event.finished_reason
                    == ReplyFinishedReason.INTERRUPTED
                )
                if interrupted_end:
                    # Handle the context when interruption
                    async for _ in self._close_unfinished_tool_calls():
                        yield _

                yield end_event

                if interrupted_end:
                    # The fallback msg goes last: Msg terminates the stream
                    yield AssistantMsg(
                        id=self.state.reply_id,
                        name=self.name,
                        content=self.react_config.interruption_message,
                        finished_reason=ReplyFinishedReason.INTERRUPTED,
                    )

    async def _inject_runtime_state(
        self,
    ) -> AsyncGenerator[HintBlockEvent, None]:
        """Inject the current runtime state (time, plan tasks and context
        usage) into the conversation context as a ``HintBlock``, so the agent
        stays aware of the information that changes across turns/replies.

        .. note:: The injection is **not** ephemeral. It is appended to the
            persistent context on purpose, so the agent can perceive how time
            elapses and what it did at each step, building a sense of time.

        .. note:: We attach a ``HintBlock`` instead of mutating the system
            prompt, so that prompt caching still works while the agent remains
            aware of the changing time / tasks / context.

        .. note:: Only information that *changes* within a conversation is
            injected here. Fixed information should live in the system prompt.

        The injection timing is decided per dimension:

        - **Time**: injected when (1) no time is recorded in the context (i.e.
          the first reply or right after a context compression), or (2) the
          elapsed time since the recorded one exceeds
          ``injection_config.time_interval`` hours. The injected time is the
          wall-clock time of ``injection_config.timezone``, and the timezone
          is injected next to it so that the elapsed time is still correct
          when the configured timezone changes within a conversation.
        - **Plan tasks**: injected when there are pending or in-progress tasks
          while the context contains neither task-related tool calls (e.g.
          they have been compressed away) nor a previous tasks injection.
        - **Context**: injected at the first iteration of a reply when the
          current input tokens are within
          ``injection_config.context_buffer_ratio`` of the compression
          threshold, letting the agent perceive that a compression is near.
          This dimension is evaluated independently of the two above.

        The user defined ``injection_config.extra_fields`` are attached to
        every injection, but never trigger one by themselves.

        Yields:
            `HintBlockEvent`:
                Emitted when a runtime-state hint is injected and
                ``injection_config.emit_hint_event`` is enabled.
        """
        if not self.injection_config.inject_runtime_state:
            return

        injections: dict = {}

        # The wall-clock time in the configured timezone. It's kept timezone
        # aware for the elapsed time calculation, while ``time_format`` decides
        # whether the timezone is carried in the injected text.
        now = datetime.now(_resolve_timezone(self.injection_config.timezone))

        # A fixed source used to detect existing injection
        injection_source = self.injection_config.injection_source

        # =====================================================================
        # Step 1: Analyze the current context
        #  - The latest injection that records a time (if any)
        #  - If the agent is already aware of the uncompleted tasks
        # =====================================================================
        task_status: dict = collections.defaultdict(int)
        for task in self.state.tasks_context.tasks:
            task_status[task.state] += 1

        has_uncompleted_tasks = (
            task_status["pending"] > 0 or task_status["in_progress"] > 0
        )

        # The text of the newest injection that records a time. An injection
        # only carries the fields triggered at that moment, so the newest one
        # doesn't necessarily record a time.
        last_time_text: str | None = None

        # The agent is aware of the tasks when the context contains the task
        # related tool calls or a previous tasks injection. Without uncompleted
        # tasks the flag is never used, so skip the detection entirely.
        aware_of_tasks = not has_uncompleted_tasks

        for msg in reversed(self.state.context):
            if last_time_text is not None and aware_of_tasks:
                # Both dimensions are settled, no need to scan the older
                # context
                break

            if msg.role != "assistant":
                continue

            for block in reversed(msg.content):
                if (
                    isinstance(block, HintBlock)
                    and block.source == injection_source
                ):
                    if isinstance(block.hint, str):
                        text = block.hint
                    else:
                        text = "".join(
                            _.text
                            for _ in block.hint
                            if isinstance(_, TextBlock)
                        )

                    if last_time_text is None and "<current-time>" in text:
                        last_time_text = text
                    if not aware_of_tasks and "<tasks>" in text:
                        aware_of_tasks = True

                elif (
                    isinstance(block, ToolCallBlock)
                    and block.name in self.injection_config.task_tool_names
                ):
                    aware_of_tasks = True

        # =====================================================================
        # Step 2: Check Time Injection
        # =====================================================================
        # No time recorded in the context, e.g. the first reply or right after
        # a context compression, so inject to be safe
        inject_time = True
        if last_time_text is not None:
            # Extract the recorded time from the injection, e.g.
            # <current-time>2026-07-01T12:00:00</current-time>
            match = re.search(
                r"<current-time>(.*?)</current-time>",
                last_time_text,
            )
            last_time = None
            if match is not None:
                try:
                    last_time = datetime.strptime(
                        match.group(1).strip(),
                        self.injection_config.time_format,
                    )
                except ValueError:
                    # Fail to parse the recorded time, e.g. the time format has
                    # changed, so inject again to be safe
                    last_time = None

            if last_time is not None:
                if last_time.tzinfo is None:
                    # The recorded time is the wall-clock time of the timezone
                    # recorded next to it, e.g.
                    # <timezone>Asia/Shanghai</timezone>. Restore it so that
                    # the comparison holds even if the configured timezone has
                    # changed since then.
                    match_tz = re.search(
                        r"<timezone>(.*?)</timezone>",
                        last_time_text,
                    )
                    last_time = last_time.replace(
                        tzinfo=_resolve_timezone(
                            match_tz.group(1).strip()
                            if match_tz
                            else self.injection_config.timezone,
                        ),
                    )

                elapsed_hours = (now - last_time).total_seconds() / 3600
                # A negative elapsed time means the recorded time is in the
                # future, e.g. the machine clock went backwards, so inject
                # again to be safe
                inject_time = not (
                    0 <= elapsed_hours <= self.injection_config.time_interval
                )

        if inject_time:
            injections["current-time"] = now.strftime(
                self.injection_config.time_format,
            )
            injections["timezone"] = self.injection_config.timezone

        # =====================================================================
        # Step 3: Check Plan Tasks
        # =====================================================================
        # If exists uncompleted tasks and the agent isn't aware of them, e.g.
        # the task related tool calls have been compressed away, so that the
        # same reminder isn't repeated on every iteration
        if has_uncompleted_tasks and not aware_of_tasks:
            injections["tasks"] = (
                f"You have {task_status['in_progress']} in-progress tasks "
                f"and {task_status['pending']} pending tasks. "
                f"Use `TaskList` to view them if you don't know."
            )

        # =====================================================================
        # Step 4: Context Length
        # =====================================================================
        # The context length is checked independently of the dimensions above,
        # and only at the beginning of a reply, where the context has just
        # grown by the new input
        if self.state.cur_iter == 0:
            # Count the current tokens
            kwargs = await self._prepare_model_input()
            input_tokens = await self.model.count_tokens(**kwargs)

            trigger_tokens = int(
                self.context_config.trigger_ratio * self.model.context_size,
            )

            if input_tokens > (
                max(
                    0.0,
                    self.context_config.trigger_ratio
                    - self.injection_config.context_buffer_ratio,
                )
                * self.model.context_size
            ):
                # To trigger memory compress
                injections["context-length"] = (
                    f"Your current context contains {input_tokens} "
                    f"tokens. When reaching {trigger_tokens} tokens, "
                    f"your context will be compressed."
                )

        if injections:
            # The user defined fields, which don't trigger an injection by
            # themselves
            injections.update(self.injection_config.extra_fields)

            hint_block = HintBlock(
                source=injection_source,
                # Use replace instead of format, so that the other curly
                # braces in the template are kept as-is
                hint=self.injection_config.template.replace(
                    "{runtime_state}",
                    "\n".join(
                        f"<{k}>{v}</{k}>" for k, v in injections.items()
                    ),
                ),
            )
            self.state.append_context(
                self.name,
                [hint_block],
            )
            if self.injection_config.emit_hint_event:
                yield HintBlockEvent(
                    reply_id=self.state.reply_id,
                    block_id=hint_block.id,
                    source=hint_block.source,
                    hint=hint_block.hint,
                )

    async def _reasoning(
        self,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncGenerator[
        ModelCallStartEvent
        | TextBlockStartEvent
        | TextBlockDeltaEvent
        | TextBlockEndEvent
        | ToolCallBlock
        | ToolCallDeltaEvent
        | ToolCallEndEvent
        | ThinkingBlockStartEvent
        | ThinkingBlockDeltaEvent
        | ThinkingBlockEndEvent
        | DataBlockStartEvent
        | DataBlockDeltaEvent
        | DataBlockEndEvent
        | ModelCallEndEvent
        | Msg,
        None,
    ]:
        """Reasoning entry point (maybe wrapped by middleware)."""
        if not self._reasoning_middlewares:
            async for item in self._reasoning_impl(tool_choice=tool_choice):
                yield item
        else:

            async def execute_chain(
                index: int = 0,
                tool_choice: ToolChoice = tool_choice,
            ) -> AsyncGenerator:
                if index >= len(self._reasoning_middlewares):
                    async for item in self._reasoning_impl(
                        tool_choice=tool_choice,
                    ):
                        yield item
                else:
                    mw = self._reasoning_middlewares[index]
                    input_kwargs = {"tool_choice": tool_choice}

                    async def next_handler(**kwargs: Any) -> AsyncGenerator:
                        async for item in execute_chain(
                            index + 1,
                            **{**input_kwargs, **kwargs},
                        ):
                            yield item

                    async for item in mw.on_reasoning(
                        agent=self,
                        input_kwargs=input_kwargs,
                        next_handler=next_handler,
                    ):
                        yield item

            async for item in execute_chain():
                yield item

    async def _reasoning_impl(
        self,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncGenerator[
        ModelCallStartEvent
        | TextBlockStartEvent
        | TextBlockDeltaEvent
        | TextBlockEndEvent
        | ToolCallBlock
        | ToolCallDeltaEvent
        | ToolCallEndEvent
        | ThinkingBlockStartEvent
        | ThinkingBlockDeltaEvent
        | ThinkingBlockEndEvent
        | DataBlockStartEvent
        | DataBlockDeltaEvent
        | DataBlockEndEvent
        | ModelCallEndEvent
        | Msg,
        None,
    ]:
        """Core reasoning logic. Yields chunks with is_last flag."""

        yield ModelCallStartEvent(
            reply_id=self.state.reply_id,
            model_name=self.model.model,
        )

        # Get the input arguments for the chat model, including messages and
        # tools
        kwargs = await self._prepare_model_input()

        # Call the chat model
        res = await self._call_model(
            tool_choice=tool_choice,
            **kwargs,
        )

        block_ids: dict = {
            "text": None,
            "thinking": None,
            "tools": [],
            "data": [],
        }
        completed_response: ChatResponse | None = None

        # Check if res is an async generator (streaming response)
        if inspect.isasyncgen(res):
            async for chunk in res:
                # Save the last chunk with completed response
                if chunk.is_last:
                    completed_response = chunk

                else:
                    # Convert the chunk into events
                    async for evt in self._convert_chat_response_to_event(
                        block_ids,
                        chunk,
                    ):
                        yield evt

        elif isinstance(res, ChatResponse):
            completed_response = res
            async for evt in self._convert_chat_response_to_event(
                block_ids,
                res,
            ):
                yield evt

        # Send the ended events for the remaining active blocks
        if block_ids["text"] is not None:
            yield TextBlockEndEvent(
                reply_id=self.state.reply_id,
                block_id=block_ids["text"],
            )
        if block_ids["thinking"] is not None:
            yield ThinkingBlockEndEvent(
                reply_id=self.state.reply_id,
                block_id=block_ids["thinking"],
            )
        for tool_call_id in block_ids["tools"]:
            yield ToolCallEndEvent(
                reply_id=self.state.reply_id,
                tool_call_id=tool_call_id,
            )
        for data_block_id in block_ids["data"]:
            yield DataBlockEndEvent(
                reply_id=self.state.reply_id,
                block_id=data_block_id,
            )

        # Guard against empty or interrupted streaming responses.
        if completed_response is None:
            raise RuntimeError(
                "Model returned an empty streaming response: no is_last=True"
                " chunk was received.  The model call may have been "
                "interrupted mid-stream (network dropout, timeout, or model "
                "bug).",
            )

        # Send the model call ended event with usage if available
        yield ModelCallEndEvent(
            reply_id=self.state.reply_id,
            input_tokens=completed_response.usage.input_tokens
            if completed_response.usage
            else 0,
            output_tokens=completed_response.usage.output_tokens
            if completed_response.usage
            else 0,
            finished_reason=completed_response.finished_reason,
        )

        self._save_to_context(
            list(completed_response.content),
            completed_response.usage,
        )

        # A thinking-only response is an intermediate reasoning step rather
        # than a user-visible final answer. Keep the ReAct loop running so the
        # model can produce text, data, or a tool call on the next iteration.
        has_only_thinking_blocks = bool(completed_response.content) and all(
            isinstance(block, ThinkingBlock)
            for block in completed_response.content
        )

        # If no tool call is generated, return the final message directly
        if (
            completed_response.finished_reason != FinishedReason.INTERRUPTED
            and not any(
                isinstance(_, ToolCallBlock)
                for _ in completed_response.content
            )
            and not has_only_thinking_blocks
        ):
            last_ctx = self._get_last_msg()
            final_usage = (
                Usage(
                    input_tokens=last_ctx.usage.input_tokens,
                    output_tokens=last_ctx.usage.output_tokens,
                )
                if last_ctx is not None and last_ctx.usage is not None
                else None
            )
            yield AssistantMsg(
                id=self.state.reply_id,
                name=self.name,
                # Text only response message
                content=list(completed_response.content),
                usage=final_usage,
                # The INTERRUPTED case is excluded by the branch condition
                finished_reason=ReplyFinishedReason.COMPLETED,
            )

    async def _check_incoming_event(
        self,
        event: UserConfirmResultEvent | ExternalExecutionResultEvent | None,
    ) -> bool:
        """Check if the agent is waiting for the incoming event, if no, raise
        error.

        Args:
            event (`UserConfirmResultEvent | ExternalExecutionResultEvent \
            | None`):
                The incoming event to be checked.

        Raises:
            `ValueError`:
                If the agent is not waiting for the incoming event, or the
                event is not valid.

        Returns:
            `bool`:
                If the agent is waiting for the incoming event, that means
                this reply calling continues from the previous one. If not,
                the reply id and iteration count should be updated for the new
                reply.
        """
        awaiting_tool_calls = self.state.get_awaiting_tool_calls(self.name)
        awaiting_confirmations = [
            _.id
            for _ in awaiting_tool_calls
            if _.state == ToolCallState.ASKING
        ]
        awaiting_external_executions = [
            _.id
            for _ in awaiting_tool_calls
            if _.state == ToolCallState.SUBMITTED
        ]

        # No incoming event but needed
        if event is None and (
            awaiting_confirmations or awaiting_external_executions
        ):
            raise ValueError(
                f"Agent is waiting for {len(awaiting_confirmations)} tool "
                f"calls and external execution results for "
                f"{len(awaiting_external_executions)} tool calls, "
                f"but received no event.",
            )

        if isinstance(event, UserConfirmResultEvent):
            if not awaiting_confirmations:
                raise ValueError(
                    f"Agent is not waiting for user confirmation, "
                    f"but received UserConfirmResultEvent: {event}",
                )

            # Given event, required but not match
            extra_ids = set(
                _.tool_call.id for _ in event.confirm_results
            ) - set(awaiting_confirmations)
            if extra_ids:
                raise ValueError(
                    f"Received UserConfirmResultEvent with tool call ids "
                    f"{extra_ids} that are not waiting for confirmation.",
                )

        if isinstance(event, ExternalExecutionResultEvent):
            if not awaiting_external_executions:
                raise ValueError(
                    f"Agent is not waiting for external execution result, "
                    f"but received ExternalExecutionResultEvent: {event}",
                )

            extra_ids = set(_.id for _ in event.execution_results) - set(
                awaiting_external_executions,
            )
            if extra_ids:
                raise ValueError(
                    f"Received ExternalExecutionResultEvent with tool call "
                    f"ids {extra_ids} that are not waiting for external "
                    f"execution results.",
                )

        return event is not None

    async def _handle_incoming_event(
        self,
        event: UserConfirmResultEvent | ExternalExecutionResultEvent | None,
    ) -> AsyncGenerator[
        ToolResultStartEvent
        | ToolResultTextDeltaEvent
        | ToolResultDataDeltaEvent
        | ToolResultEndEvent,
        None,
    ]:
        """Handle the incoming event and update the context accordingly.

        Args:
            event (`UserConfirmResultEvent | ExternalExecutionResultEvent \
            | None`):
                The incoming event to be handled.

        Yields:
            `ToolResultStartEvent \
            | ToolResultTextDeltaEvent \
            | ToolResultDataDeltaEvent \
            | ToolResultEndEvent`:
                The events generated during the handling of the incoming event.
        """
        # Return directly if no event
        if event is None or len(self.state.context) == 0:
            return

        if isinstance(event, UserConfirmResultEvent):
            # The confirmed tool calls
            confirmed_tool_calls = {
                _.tool_call.id: _ for _ in event.confirm_results
            }

            # Update the state with the confirmed tool calls
            last_msg = self.state.context[-1]
            for tool_call in last_msg.get_content_blocks("tool_call"):
                if len(confirmed_tool_calls) == 0:
                    break

                if tool_call.id in confirmed_tool_calls:
                    confirmation = confirmed_tool_calls[tool_call.id]
                    if confirmation.confirmed:
                        # Update state and wait for execution in the next step
                        self._update_tool_call_state(
                            tool_call.id,
                            ToolCallState.ALLOWED,
                        )

                        # Update name and  input in case user modification is
                        # allowed
                        tool_call.name = confirmation.tool_call.name
                        tool_call.input = confirmation.tool_call.input

                        # Update the permission rule if accepted
                        if confirmation.rules:
                            for rule in confirmation.rules:
                                self._engine.add_rule(rule)

                    else:
                        # Update the state to deny and handling
                        async for evt in self._handle_error_tool_call(
                            tool_call,
                            message=(
                                "<system-reminder>The execution of tool "
                                f'"{tool_call.name}" is denied by user!'
                                "</system-reminder>"
                            ),
                            state=ToolResultState.DENIED,
                        ):
                            yield evt

                    # Delete for quick lookup and later processing
                    confirmed_tool_calls.pop(tool_call.id)

        elif isinstance(event, ExternalExecutionResultEvent):
            # Directly append the execution results into context
            for tool_result in event.execution_results:
                async for evt in self._convert_tool_chunk_to_event(
                    tool_result.id,
                    tool_result.output,
                ):
                    yield evt

                yield ToolResultEndEvent(
                    reply_id=self.state.reply_id,
                    tool_call_id=tool_result.id,
                    state=tool_result.state,
                    metadata=tool_result.metadata,
                )

                self._save_to_context([tool_result])

                # Update the state according to the execution result state
                self._update_tool_call_state(
                    tool_result.id,
                    ToolCallState.FINISHED,
                )

        else:
            raise ValueError(f"Invalid event type: {event}")

    async def _handle_incoming_messages(
        self,
        msgs: Msg | list[Msg] | None,
    ) -> None:
        """Check and handle the incoming messages before the reasoning-acting
        loop."""
        if msgs:
            copied_msgs: list = deepcopy(msgs)
            if isinstance(copied_msgs, Msg):
                copied_msgs = [copied_msgs]
            for msg in copied_msgs:
                if (
                    not isinstance(msg, Msg)
                    or msg.role == "system"
                    or msg.has_content_blocks(
                        ["tool_call", "tool_result", "thinking"],
                    )
                ):
                    raise ValueError(
                        f"Invalid message in the input: {msg}. "
                        f"The message should be a Msg object with "
                        f"role 'user' or 'assistant', "
                        f"and should not contain tool calls, "
                        f"tool results or thinking blocks.",
                    )

                self.state.context.append(msg)

    async def _batch_tool_calls(
        self,
        tool_calls: list[ToolCallBlock],
    ) -> list[_ToolCallBatch]:
        """Batch the tool calls into a sequence of batches that should be
        executed **sequentially** or **concurrently** according to the tool
        properties `is_concurrency_safe` and `is_read_only`.
        """

        # Batch the tool calls according to whether they can be executed
        # concurrently or not
        batches: list[_ToolCallBatch] = []
        for tool_call in tool_calls:
            tool = await self.toolkit.get_tool(tool_call.name)

            # Treat unregistered or unavailable tools as concurrent tools since
            # it will not generate side effects and be blocked with acting
            if tool is None or tool.is_concurrency_safe:
                if len(batches) == 0 or batches[-1].type != "concurrent":
                    batches.append(
                        _ToolCallBatch(
                            type="concurrent",
                            tool_calls=[tool_call],
                        ),
                    )
                else:
                    batches[-1].tool_calls.append(tool_call)
            else:
                if len(batches) == 0 or batches[-1].type != "sequential":
                    batches.append(
                        _ToolCallBatch(
                            type="sequential",
                            tool_calls=[tool_call],
                        ),
                    )
                else:
                    batches[-1].tool_calls.append(tool_call)

        return batches

    async def _execute_sequential_tool_calls(
        self,
        tool_calls: list[ToolCallBlock],
    ) -> AsyncGenerator[
        RequireUserConfirmEvent
        | RequireExternalExecutionEvent
        | ToolResultStartEvent
        | ToolResultTextDeltaEvent
        | ToolResultDataDeltaEvent
        | ToolResultEndEvent,
        None,
    ]:
        """Execute the given tool calls sequentially and yield the events.

        If "RequireUserConfirmEvent" or "RequireExternalExecutionEvent" is
        yielded during the execution, the execution will be paused in the
        sequential mode and wait for the outside trigger events.

        Args:
            tool_calls (`list[ToolCallBlock]`):
                The tool calls to be executed sequentially.

        Yields:
            `RequireUserConfirmEvent \
            | RequireExternalExecutionEvent \
            | ToolResultStartEvent \
            | ToolResultTextDeltaEvent \
            | ToolResultDataDeltaEvent \
            | ToolResultEndEvent`:
                The events generated during the execution of the tool calls.
        """
        break_execution = False
        for tool_call in tool_calls:
            async for evt in self._execute_tool_call(tool_call):
                yield evt
                if isinstance(
                    evt,
                    (
                        RequireUserConfirmEvent,
                        RequireExternalExecutionEvent,
                    ),
                ) or (
                    isinstance(evt, ToolResultEndEvent)
                    and evt.state == ToolResultState.INTERRUPTED
                ):
                    break_execution = True
                    break
            if break_execution:
                break

    async def _execute_concurrent_tool_calls(
        self,
        tool_calls: list[ToolCallBlock],
    ) -> AsyncGenerator[
        RequireUserConfirmEvent
        | RequireExternalExecutionEvent
        | ToolResultStartEvent
        | ToolResultTextDeltaEvent
        | ToolResultDataDeltaEvent
        | ToolResultEndEvent,
        None,
    ]:
        """Execute the given tool calls concurrently and yield the events.

        All tool calls are executed concurrently. If one or more tool calls
        fail, the remaining ones are **not** cancelled and will run to
        completion. After all tool calls finish, every exception is collected
        and re-raised together as an :py:exc:`ExceptionGroup` so the caller
        can inspect each failure individually.

        The event stream is guaranteed to be complete: the loop exits only
        after a sentinel value placed by the gather task is received, which
        means every ``queue.put`` from every worker has already finished
        before the generator returns.

        If the caller task is cancelled from outside, the concurrent worker
        tasks are cancelled explicitly (to avoid orphan tasks), any events
        already queued by the workers (including interruption chunks emitted
        by ``toolkit.call_tool`` when it catches ``CancelledError``) are
        flushed to the caller, and the generator returns normally. The
        caller is expected to detect the interruption via the flushed
        ``ToolResultEndEvent(state=INTERRUPTED)`` events, mirroring the
        event-based propagation used by
        :meth:`_execute_sequential_tool_calls`.

        Args:
            tool_calls (`list[ToolCallBlock]`):
                The tool calls to be executed concurrently.

        Yields:
            `RequireUserConfirmEvent \
            | RequireExternalExecutionEvent \
            | ToolResultStartEvent \
            | ToolResultTextDeltaEvent \
            | ToolResultDataDeltaEvent \
            | ToolResultEndEvent`:
                The events generated during the execution of the tool calls.

        Raises:
            `ExceptionGroup`:
                Raised after all tool calls finish when one or more of them
                raised an exception. Each individual exception is included in
                the group.
        """
        # A sentinel object that signals all worker tasks have finished and
        # all events have already been put into the queue.
        sentinel = object()

        # Create a queue to collect events from all concurrent workers.
        queue: Queue = Queue()

        # Batch-shared accumulator for confirmation de-duplication: the
        # suggested rules of every confirmation surfaced by this batch are
        # collected here so a later call already covered by an earlier
        # call's rule is not prompted a second time. Mutated only from
        # synchronous sections of the workers, so no lock is needed on the
        # single-threaded event loop.
        kept_rules: list[PermissionRule] = []

        async def _run_all() -> list[BaseException | None]:
            """Run all tool calls concurrently and push the sentinel when done.

            Returns:
                `list[BaseException | None]`:
                    One entry per tool call. Each entry is either ``None``
                    (success) or the exception raised by that tool call.
            """
            # return_exceptions=True keeps all tasks running even when some
            # fail, and returns exceptions as values instead of re-raising.
            results = await asyncio.gather(
                *[
                    self._into_queue(tc, queue, kept_rules)
                    for tc in tool_calls
                ],
                return_exceptions=True,
            )
            # The sentinel is placed AFTER gather returns, which guarantees
            # that every queue.put inside _into_queue has already completed.
            await queue.put(sentinel)
            return results  # type: ignore[return-value]

        gather_task = asyncio.create_task(_run_all())

        try:
            # Drain the queue until the sentinel is encountered.
            while True:
                event = await queue.get()
                if event is sentinel:
                    break
                yield event
        except asyncio.CancelledError:
            # Cancel the gather tasks, which will be handled within the toolkit
            gather_task.cancel()
            try:
                await gather_task
            except asyncio.CancelledError:
                pass
            while True:
                try:
                    event = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if event is sentinel:
                    continue
                yield event
            # Consume the cancel so this generator returns normally. The
            # caller relies on the flushed ``ToolResultEndEvent(state=
            # INTERRUPTED)`` events, not on the exception, to detect the
            # interruption — mirroring the event-based propagation used by
            # :meth:`_execute_sequential_tool_calls`.
            asyncio.current_task().uncancel()
            return

        # All tasks are done at this point; collect and re-raise exceptions.
        results = await gather_task
        exceptions = [r for r in results if isinstance(r, Exception)]
        if exceptions:
            raise ExceptionGroup(
                "One or more tool calls raised an exception",
                exceptions,
            )

    async def _into_queue(
        self,
        tool_call: ToolCallBlock,
        queue: Queue,
        kept_rules: list[PermissionRule] | None = None,
    ) -> None:
        """Execute a single tool call and forward every event into *queue*.

        Args:
            tool_call (`ToolCallBlock`):
                The tool call to execute.
            queue (`Queue`):
                The shared async queue that collects events from all
                concurrent workers.
            kept_rules (`list[PermissionRule] | None`, defaults to `None`):
                The batch-shared accumulator of already-surfaced suggested
                rules, forwarded to :meth:`_execute_tool_call` for
                confirmation de-duplication within the concurrent batch.
        """
        async for evt in self._execute_tool_call(tool_call, kept_rules):
            await queue.put(evt)

    async def _execute_tool_call(
        self,
        tool_call: ToolCallBlock,
        kept_rules: list[PermissionRule] | None = None,
    ) -> AsyncGenerator[
        RequireUserConfirmEvent
        | RequireExternalExecutionEvent
        | ToolResultStartEvent
        | ToolResultTextDeltaEvent
        | ToolResultDataDeltaEvent
        | ToolResultEndEvent,
        None,
    ]:
        """Execute a single tool call with permission checking and context
        management.

        This method handles the full tool call lifecycle: input validation,
        permission checking, event emission, and context writes.  The raw
        tool execution (``toolkit.call_tool``) is delegated to
        :meth:`_acting`, which is the hook point for ``on_acting``
        middleware.

        Args:
            tool_call (`ToolCallBlock`):
                The tool call block to be executed.
            kept_rules (`list[PermissionRule] | None`, defaults to `None`):
                A batch-scoped, shared accumulator of the suggested rules
                already surfaced by earlier confirmations in the same
                concurrent batch. Passed only by
                :meth:`_execute_concurrent_tool_calls`; when provided, a
                non-safety ASK whose invocation is already covered by an
                accumulated rule is de-duplicated (left ``PENDING`` and
                not surfaced again). ``None`` disables de-duplication
                (e.g. sequential execution, which already parks at the
                first ASK).

        Yields:
            `RequireUserConfirmEvent \
            | RequireExternalExecutionEvent \
            | ToolResultStartEvent \
            | ToolResultTextDeltaEvent \
            | ToolResultDataDeltaEvent \
            | ToolResultEndEvent`:
                The events generated during the tool call execution.
        """
        # ===================================================================
        # Step 1: Check and parse the tool call input:
        #  - if failed, directly return the error message to the agent
        #  - if success, continue to permission checking and tool execution
        # ===================================================================
        try:
            # Check if the tool is available
            tool = await self.toolkit.check_tool_available(
                tool_call.name,
                self.state.tool_context.activated_groups,
            )

            # Try to parse the input with the tool schema
            parsed_input = _json_loads_with_repair(
                tool_call.input,
                tool.input_schema,
            )

            # Validate the parsed input with the tool schema
            # TODO: Maybe some logic to mix the validation error in runtime
            try:
                jsonschema.validate(parsed_input, tool.input_schema)
            except jsonschema.ValidationError as e:
                raise AgentOrientedException(
                    f"Input validation failed for tool '{tool_call.name}': "
                    f"{e.message}",
                ) from e

        # The exceptions that
        #  - cannot found tool
        #  - tool not available
        #  - input parsing failure
        except AgentOrientedException as e:
            async for evt in self._handle_error_tool_call(
                tool_call,
                e.message,
                state=ToolResultState.ERROR,
            ):
                yield evt

            return

        # ===================================================================
        # Step 2: Check permission by toolkit and permission engine
        # ===================================================================
        if tool_call.state == ToolCallState.ALLOWED:
            # Already allowed by user confirmation, skip permission checking
            decision = PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="Already allowed by user confirmation.",
            )
        else:
            decision = await self._engine.check_permission(
                tool,
                parsed_input,
            )

        # ===================================================================
        # Step 3: Handle the permission and execute the tool call if allowed
        # ===================================================================

        # Case 1: Ask for user confirmation if needed
        if decision.behavior in [
            PermissionBehavior.ASK,
            PermissionBehavior.PASSTHROUGH,
        ]:
            # Batch de-duplication (concurrent batches only): if an earlier
            # confirmation in this same batch already suggested an allow rule
            # that matches this invocation, do not surface a second prompt.
            # Leave the call PENDING so the next reply run re-evaluates it
            # against the engine once the user answers the first prompt (and
            # its rule has been added). Safety ASKs (bypass-immune) are never
            # de-duplicated — an allow rule cannot clear them, so each must
            # surface its own prompt.
            is_safety_ask = (
                decision.behavior == PermissionBehavior.ASK
                and decision.bypass_immune
            )
            if kept_rules is not None and not is_safety_ask:
                for rule in kept_rules:
                    if rule.tool_name != tool.name:
                        continue
                    if await _execute_async_or_sync_func(
                        tool.match_rule,
                        rule.rule_content,
                        parsed_input,
                    ):
                        # Covered by an earlier call's rule; stay PENDING and
                        # do not yield — re-evaluated on the next reply run.
                        return

            if kept_rules is not None:
                # Register this prompt's suggested rules so later calls in the
                # batch can be de-duplicated against them.
                kept_rules.extend(decision.suggested_rules or [])

            # Set the state of the tool call to "ask"
            # **Note** the update must be done before yielding the event
            self._update_tool_call_state(
                tool_call.id,
                ToolCallState.ASKING,
            )

            tool_call.suggested_rules = decision.suggested_rules or []
            yield RequireUserConfirmEvent(
                reply_id=self.state.reply_id,
                tool_calls=[tool_call],
            )
            return

        # Case 2: Denied by the permission system
        if decision.behavior == PermissionBehavior.DENY:
            async for evt in self._handle_error_tool_call(
                tool_call,
                decision.message,
                state=ToolResultState.DENIED,
            ):
                yield evt

            return

        # Case 3: Allowed by the permission system, execute the tool call and
        #  yield the events
        if decision.behavior == PermissionBehavior.ALLOW:
            self._update_tool_call_state(
                tool_call.id,
                ToolCallState.ALLOWED,
            )
            # Send start event
            yield ToolResultStartEvent(
                reply_id=self.state.reply_id,
                tool_call_id=tool_call.id,
                tool_call_name=tool_call.name,
            )
            # Send requiring external execution event if it's an external tool
            if tool.is_external_tool:
                # Update the state to "submitted" BEFORE yielding
                # because the outer loop will break immediately after
                # receiving this event, preventing any code after yield
                # from executing
                self._update_tool_call_state(
                    tool_call.id,
                    ToolCallState.SUBMITTED,
                )
                yield RequireExternalExecutionEvent(
                    reply_id=self.state.reply_id,
                    tool_calls=[tool_call],
                )
                return

            # ================================================================
            # Step 4: Delegate raw execution to _acting (middleware hook point)
            # ================================================================
            async for chunk in self._acting(tool_call):
                # The ToolResponse is the last and completed tool result here
                if isinstance(chunk, ToolResponse):
                    tool_result_block = ToolResultBlock(
                        id=tool_call.id,
                        name=tool_call.name,
                        output=[TextBlock(text=chunk.content)]
                        if isinstance(chunk.content, str)
                        else chunk.content,
                        state=chunk.state,
                        metadata=chunk.metadata,
                    )

                    # ========================================================
                    # Step 5: Truncate the tool result if exceed
                    # ========================================================
                    (
                        reserved_tool_result_block,
                        offload_tool_result_block,
                    ) = await self._split_tool_result_for_compression(
                        tool_result_block,
                    )

                    # If offload result is not empty, attach reminder to the
                    # reserved context
                    if offload_tool_result_block is not None:
                        reminder = (
                            "\n<<<TRUNCATED>>>\n<system-reminder>The "
                            "remaining content has been omitted for "
                            "limited context.{offload_reminder}"
                            "</system-reminder>"
                        )

                        offload_reminder = ""
                        if self.offloader:
                            path = await self.offloader.offload_tool_result(
                                self.state.session_id,
                                offload_tool_result_block,
                            )

                            offload_reminder = (
                                f" You can refer to the file in '{path}' "
                                f"for the truncated content if needed."
                            )

                        reminder = reminder.format(
                            offload_reminder=offload_reminder,
                        )

                        # Insert the reminder to the tool result output
                        if isinstance(reserved_tool_result_block.output, str):
                            reserved_tool_result_block.output += reminder

                        elif len(
                            reserved_tool_result_block.output,
                        ) > 0 and isinstance(
                            reserved_tool_result_block.output[-1],
                            TextBlock,
                        ):
                            reserved_tool_result_block.output[
                                -1
                            ].text += reminder

                        else:
                            reserved_tool_result_block.output += [
                                TextBlock(text=reminder),
                            ]

                    self._save_to_context([reserved_tool_result_block])
                    # Ends the tool call lifecycle.
                    self._update_tool_call_state(
                        tool_call.id,
                        ToolCallState.FINISHED,
                    )
                    # The ended event for the tool result
                    yield ToolResultEndEvent(
                        reply_id=self.state.reply_id,
                        tool_call_id=tool_call.id,
                        state=chunk.state,
                        metadata=chunk.metadata,
                    )

                else:
                    # Intermediate ToolChunk — convert to streaming events
                    async for evt in self._convert_tool_chunk_to_event(
                        tool_call.id,
                        chunk.content,
                    ):
                        yield evt

            return

        raise ValueError(
            f"Invalid permission decision behavior: {decision.behavior}",
        )

    async def _acting(
        self,
        tool_call: ToolCallBlock,
    ) -> AsyncGenerator["ToolChunk | ToolResponse", None]:
        """Raw tool execution entry point (maybe wrapped by middleware).

        This method is the hook point for ``on_acting`` middleware.  It
        delegates to :meth:`_acting_impl` which wraps
        ``toolkit.call_tool`` directly.  Permission checking and context
        writes are **not** part of this method — they are handled by
        :meth:`_execute_tool_call` before and after this call.

        Args:
            tool_call (`ToolCallBlock`):
                The tool call block to execute.

        Yields:
            `ToolChunk | ToolResponse`:
                Intermediate :class:`~agentscope.tool.ToolChunk` objects
                followed by a final :class:`~agentscope.tool.ToolResponse`.
        """
        if not self._acting_middlewares:
            async for item in self._acting_impl(tool_call):
                yield item
        else:

            async def execute_chain(
                index: int = 0,
                tool_call: ToolCallBlock = tool_call,
            ) -> AsyncGenerator:
                if index >= len(self._acting_middlewares):
                    async for item in self._acting_impl(tool_call):
                        yield item
                else:
                    mw = self._acting_middlewares[index]
                    input_kwargs = {"tool_call": tool_call}

                    async def next_handler(**kwargs: Any) -> AsyncGenerator:
                        async for item in execute_chain(
                            index + 1,
                            **{**input_kwargs, **kwargs},
                        ):
                            yield item

                    async for item in mw.on_acting(
                        agent=self,
                        input_kwargs=input_kwargs,
                        next_handler=next_handler,
                    ):
                        yield item

            async for item in execute_chain():
                yield item

    async def _acting_impl(
        self,
        tool_call: ToolCallBlock,
    ) -> AsyncGenerator["ToolChunk | ToolResponse", None]:
        """Core tool execution logic.

        Wraps :meth:`~agentscope.tool.Toolkit.call_tool` and yields its
        output unchanged.  Does **not** perform permission checking or
        write to the agent context — those responsibilities belong to
        :meth:`_execute_tool_call`.

        .. note::
            Tools with ``is_state_injected=True`` receive the live
            ``agent.state`` object.  Offloading such tools to a background
            task (via ``on_acting`` middleware) may cause concurrent state
            mutations.  TODO: block background offloading for
            state-injected tools.

        Args:
            tool_call (`ToolCallBlock`):
                The tool call block to execute.

        Yields:
            `ToolChunk | ToolResponse`:
                Intermediate :class:`~agentscope.tool.ToolChunk` objects
                followed by a final :class:`~agentscope.tool.ToolResponse`.
        """
        async for chunk in self.toolkit.call_tool(tool_call, self.state):
            yield chunk

    async def _handle_error_tool_call(
        self,
        tool_call: ToolCallBlock,
        message: str,
        state: ToolResultState,
    ) -> AsyncGenerator[
        ToolResultStartEvent
        | ToolResultTextDeltaEvent
        | ToolResultDataDeltaEvent
        | ToolResultEndEvent,
        None,
    ]:
        """A quick handling for the non-streaming tool results, and ends the
        lifecycle of the tool call by updating its state to "finished".

        Args:
            tool_call (`ToolCallBlock`):
                The tool call block that has errors.
            message (`str`):
                The error message to be returned for the tool call.
            state (`ToolResultState`):
                The state of the tool result, such as "error" or "denied".

        Yields:
            `ToolResultStartEvent \
            | ToolResultTextDeltaEvent \
            | ToolResultDataDeltaEvent \
            | ToolResultEndEvent`:
                The events generated for the error tool call.
        """

        yield ToolResultStartEvent(
            reply_id=self.state.reply_id,
            tool_call_id=tool_call.id,
            tool_call_name=tool_call.name,
        )

        result = ToolChunk(
            content=[TextBlock(text=message)],
            state=state,
        )

        # Return the result directly to the agent
        self._save_to_context(
            [
                ToolResultBlock(
                    id=tool_call.id,
                    name=tool_call.name,
                    output=message,
                    state=state,
                ),
            ],
        )

        async for evt in self._convert_tool_chunk_to_event(
            tool_call.id,
            result.content,
        ):
            yield evt

        yield ToolResultEndEvent(
            reply_id=self.state.reply_id,
            tool_call_id=tool_call.id,
            state=state,
        )

        self._update_tool_call_state(
            tool_call.id,
            ToolCallState.FINISHED,
        )

    # =======================================================================
    # Context management related methods
    # =======================================================================

    async def _split_context_for_compression(
        self,
        to_reserved_tokens: float,
        tools: list[dict],
    ) -> tuple[list[Msg], list[Msg]]:
        """Split context into parts to compress and parts to keep recent.

        Args:
            to_reserved_tokens (`float`):
                The tokens to be reserved.
            tools (`list[dict]`):
                The tools JSON schemas used for token counting.

        Returns:
            `tuple[list[Msg], list[Msg]]`:
                The message objects to be compressed and reserved during
                context compression.
        """

        # The system prompt
        system_msg = [
            SystemMsg(name="system", content=await self._get_system_prompt()),
        ]

        # Append the current summary if exists
        if self.state.summary:
            system_msg.append(
                UserMsg("user", self.state.summary),
            )

        msg_index = len(self.state.context) - 1
        while msg_index >= 0:
            # Count the tokens when msgs after msg_index are reserved
            reserved_tokens = await self.model.count_tokens(
                system_msg + self.state.context[msg_index:],
                tools,
            )
            # If reserved tokens exceed the limit
            if reserved_tokens >= to_reserved_tokens:
                break
            msg_index -= 1

        if msg_index < 0:
            return [], deepcopy(self.state.context)

        # The msgs that won't exceed the reserved token limit
        msgs_to_compress = self.state.context[:msg_index]
        msgs_to_reserve = self.state.context[msg_index + 1 :]
        boundary_msg = self.state.context[msg_index]

        # Handle the boundary Msg
        boundary_msg_to_compress = deepcopy(boundary_msg)
        boundary_msg_to_reserve = deepcopy(boundary_msg)

        attempt_msg = deepcopy(boundary_msg)

        boundary_msg_content = boundary_msg.get_content_blocks()
        block_index = len(boundary_msg_content) - 1
        while block_index >= 0:
            attempt_msg.content = boundary_msg_content[block_index:]

            try_reserved = system_msg + [attempt_msg] + msgs_to_reserve
            reserved_tokens = await self.model.count_tokens(
                try_reserved,
                tools,
            )
            if reserved_tokens > to_reserved_tokens:
                break
            block_index -= 1

        # Adjust the block_index to avoid splitting tool call and result pairs.
        # Moving the boundary can bring another tool call into the compressed
        # part while leaving its result reserved, so repeat until it is stable.
        while True:
            # Check if the reserved part has tool results that don't have the
            # corresponding tool calls
            remain_result_ids = {}
            for i in range(
                len(boundary_msg_content) - 1,
                block_index,
                -1,
            ):
                block = boundary_msg_content[i]
                if isinstance(block, ToolResultBlock):
                    remain_result_ids[block.id] = i
                elif isinstance(block, ToolCallBlock):
                    remain_result_ids.pop(block.id, None)

            # All tool result blocks in the reserved part are paired.
            if not remain_result_ids:
                break

            # Move unmatched results into the compressed part and recheck,
            # because this move can split another tool call/result pair.
            block_index = max(remain_result_ids.values())

        # Split the boundary msg content
        boundary_msg_to_compress.content = boundary_msg_content[
            : block_index + 1
        ]
        boundary_msg_to_reserve.content = boundary_msg_content[
            block_index + 1 :
        ]

        if len(boundary_msg_to_compress.content) > 0:
            msgs_to_compress += [boundary_msg_to_compress]

        if len(boundary_msg_to_reserve.content) > 0:
            msgs_to_reserve = [boundary_msg_to_reserve] + msgs_to_reserve

        return msgs_to_compress, msgs_to_reserve

    async def _clear_unreserved_read_cache(
        self,
        msgs_to_reserve: list[Msg],
    ) -> None:
        """Clean Read caches not referenced by reserved Read tool calls."""
        reserved_paths: set[str] = set()
        for msg in msgs_to_reserve:
            for block in msg.get_content_blocks("tool_call"):
                if not (
                    isinstance(block, ToolCallBlock) and block.name == "Read"
                ):
                    continue

                try:
                    tool_input = _json_loads_with_repair(block.input)
                except Exception:  # pylint: disable=broad-exception-caught
                    continue

                file_path = tool_input.get("file_path")
                if isinstance(file_path, str):
                    reserved_paths.add(file_path)

        await self.state.tool_context.clean_file_cache(
            reserved_file_paths=reserved_paths,
        )

    async def _split_tool_result_for_compression(
        self,
        tool_result: ToolResultBlock,
    ) -> tuple[ToolResultBlock, ToolResultBlock | None]:
        """Split the tool result for compression.

        Args:
            tool_result (`ToolResultBlock`):
                The tool result block.

        Returns:
            `tuple[ToolResultBlock, ToolResultBlock | None]`:
                A tuple of the tool result blocks to reserved in context and
                to offload (if any).
        """
        n_tokens = await self.model.count_tokens(
            [AssistantMsg(self.name, content=tool_result.output)],
            None,
        )

        # Return the tool result without truncation
        if n_tokens <= self.context_config.tool_result_limit:
            return tool_result, None

        # Use a copied block for token counting
        copied_tool_result = deepcopy(tool_result)

        # Normalized into content blocks
        if isinstance(copied_tool_result.output, str):
            copied_tool_result.output = [
                TextBlock(text=copied_tool_result.output),
            ]

        # Find the index of the block that will exceed the limit
        boundary_index = 0
        for i in range(len(copied_tool_result.output) - 1, 0, -1):
            copied_tool_result.output = tool_result.output[:i]
            cur_tokens = await self.model.count_tokens(
                [
                    AssistantMsg(
                        self.name,
                        content=copied_tool_result.output,
                    ),
                ],
                None,
            )
            if cur_tokens < self.context_config.tool_result_limit:
                boundary_index = i
                break

        # The blocks to reserve and offload (deep copy to avoid
        # modifying original)
        reserved_blocks: list = [
            deepcopy(b) for b in tool_result.output[:boundary_index]
        ]
        offload_blocks: list = [
            deepcopy(b) for b in tool_result.output[boundary_index + 1 :]
        ]

        # Get the boundary block, if text block, we can truncate it
        boundary_block = tool_result.output[boundary_index]
        if isinstance(boundary_block, TextBlock):
            # Truncate it
            truncated_text = boundary_block.text
            cur_tokens = await self.model.count_tokens(
                [AssistantMsg(self.name, content=reserved_blocks)],
                None,
            )
            cur_tokens_plus = await self.model.count_tokens(
                [
                    AssistantMsg(
                        self.name,
                        content=reserved_blocks + [boundary_block],
                    ),
                ],
                None,
            )
            # Truncate the text by proportion of tokens
            token_delta = cur_tokens_plus - cur_tokens
            remaining_token_budget = (
                self.context_config.tool_result_limit - cur_tokens
            )
            if token_delta <= 0:
                reserved_tokens = (
                    len(truncated_text) if remaining_token_budget > 0 else 0
                )
            else:
                reserved_tokens = int(
                    remaining_token_budget / token_delta * len(truncated_text),
                )
            reserved_tokens = max(
                0,
                min(len(truncated_text), reserved_tokens),
            )

            reserved_text = truncated_text[:reserved_tokens]
            offload_text = truncated_text[reserved_tokens:]

            if reserved_text:
                if (
                    len(reserved_blocks) > 0
                    and reserved_blocks[-1].type == "text"
                ):
                    reserved_blocks[-1].text += reserved_text

                else:
                    reserved_blocks.append(
                        TextBlock(text=reserved_text, id=boundary_block.id),
                    )

            if offload_text:
                if (
                    len(offload_blocks) > 0
                    and offload_blocks[0].type == "text"
                ):
                    offload_blocks[0].text = (
                        offload_text + offload_blocks[0].text
                    )

                else:
                    offload_blocks.insert(
                        0,
                        TextBlock(text=offload_text, id=boundary_block.id),
                    )

        else:
            # Drop the boundary block if inseparable
            offload_blocks.insert(0, boundary_block)

        if len(offload_blocks) == 0:
            return tool_result, None

        # Create new ToolResultBlock instances for reserved and offload
        reserved_tool_result = ToolResultBlock(
            id=tool_result.id,
            name=tool_result.name,
            output=reserved_blocks,
            state=tool_result.state,
        )
        offload_tool_result = ToolResultBlock(
            id=tool_result.id,
            name=tool_result.name,
            output=offload_blocks,
            state=tool_result.state,
        )

        return reserved_tool_result, offload_tool_result

    # ======================================================================
    # Agent internal utility methods
    # ======================================================================
    async def _get_system_prompt(self) -> str:
        """Get the system prompt of the agent."""
        prompt = [self._system_prompt]

        # Skill related instructions
        skill_instructions = await self.toolkit.get_skill_instructions(
            self.state.tool_context.activated_groups,
        )
        if skill_instructions:
            prompt.append(skill_instructions)

        # Workspace & offloader instructions
        if isinstance(self.offloader, WorkspaceBase):
            offload_instructions = await self.offloader.get_instructions()
            if offload_instructions:
                prompt.append(offload_instructions)

        result = "\n".join(prompt)

        # Apply system_prompt middlewares sequentially (transformer pattern)
        for mw in self._system_prompt_middlewares:
            result = await mw.on_system_prompt(self, result)

        return result

    async def _prepare_model_input(self) -> dict[str, Any]:
        """A unified method to prepare the chat model input according to
        the current context.

        Returns:
            `dict[str, Any]`
                The keyword arguments passed to the model.
        """
        # The system prompt
        messages = [
            SystemMsg(name="system", content=await self._get_system_prompt()),
        ]
        # The compressed summary
        if self.state.summary:
            messages.append(
                UserMsg(name="user", content=self.state.summary),
            )
        # The conversation context
        messages.extend(self.state.context)

        # Get the tools schemas
        tools = await self.toolkit.get_tool_schemas(
            self.state.tool_context.activated_groups,
        )

        return {
            "messages": messages,
            "tools": tools,
        }

    async def _call_model(
        self,
        messages: list[Msg],
        tools: list[dict],
        tool_choice: ToolChoice | None = None,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Perform model inference with retry logic and middleware support.

        Args:
            messages (`list[Msg]`):
                The input messages to the model.
            tools (`list[dict]`):
                The function schemas of the tools.
            tool_choice (`ToolChoice | None`, optional):
                The tool choice strategy for the model call.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                The model response, which can be a `ChatResponse` for
                non-streaming models, or an async generator yielding
                `ChatResponse` chunks for streaming models.
        """
        models = [self.model]

        # Fallback to the secondary model if the primary model fails after
        # retries
        if self.model_config.fallback_model:
            models.append(self.model_config.fallback_model)

        last_exception = None
        # ``max_retries`` is the number of retries on top of the initial
        # call (mirrors ``ChatModelBase.max_retries``), so total attempts
        # per model is ``max_retries + 1``.
        for index, model in enumerate(models):
            if index > 0:
                logger.info(
                    "Fallback to model '%s'",
                    model.model,
                )

            for attempt in range(self.model_config.max_retries + 1):
                try:
                    # Apply middleware to wrap the actual model() call
                    if not self._model_call_middlewares:
                        return await model(
                            messages=messages,
                            tools=tools,
                            tool_choice=tool_choice,
                        )
                    else:
                        # pylint: disable=cell-var-from-loop
                        async def execute_chain(
                            index: int = 0,
                            current_model: ChatModelBase = model,
                            messages: list[Msg] = messages,
                            tools: list[dict] = tools,
                            tool_choice: ToolChoice = tool_choice,
                        ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
                            """Execute the model chain."""
                            if index >= len(self._model_call_middlewares):
                                return await current_model(
                                    messages=messages,
                                    tools=tools,
                                    tool_choice=tool_choice,
                                )
                            else:
                                mw = self._model_call_middlewares[index]
                                input_kwargs = {
                                    "current_model": current_model,
                                    "messages": messages,
                                    "tools": tools,
                                    "tool_choice": tool_choice,
                                }

                                async def next_handler(
                                    **kwargs: Any,
                                ) -> (
                                    ChatResponse
                                    | AsyncGenerator[ChatResponse, None]
                                ):
                                    # pylint: disable=cell-var-from-loop
                                    return await execute_chain(
                                        index + 1,
                                        **{**input_kwargs, **kwargs},
                                    )

                                return await mw.on_model_call(
                                    agent=self,
                                    input_kwargs=input_kwargs,
                                    next_handler=next_handler,
                                )

                        return await execute_chain()
                except Exception as e:
                    last_exception = e
                    # Only log a "Retrying" message when there's actually a
                    # next attempt left for this model. When ``max_retries=0``
                    # or the last retry has been used, the outer loop either
                    # falls over to the fallback or raises.
                    if attempt < self.model_config.max_retries:
                        logger.warning(
                            "Model %s call failed for agent %s. "
                            "Retrying (%d/%d)...",
                            model.model,
                            self.name,
                            attempt + 1,
                            self.model_config.max_retries,
                        )
                    else:
                        logger.warning(
                            "Model %s exhausted all %d attempt(s) "
                            "for agent %s.",
                            model.model,
                            self.model_config.max_retries + 1,
                            self.name,
                        )

        if last_exception:
            raise last_exception from None

        raise RuntimeError(
            "Model call failed after retries, but no exception was raised.",
        )

    def _update_tool_call_state(
        self,
        tool_call_id: str,
        state: ToolCallState,
    ) -> None:
        """Update the tool call state. This function is to avoid the update
        not reflected in the context due to the shallow copy of the content
        blocks somewhere in the code.

        Args:
            tool_call_id (`str`):
                The tool call id to be updated.
            state (`ToolCallState`):
                The new state of the tool call.
        """
        if len(self.state.context) == 0:
            return
        last_msg = self.state.context[-1]
        if last_msg.role != "assistant" or last_msg.name != self.name:
            return
        for block in last_msg.get_content_blocks():
            if isinstance(block, ToolCallBlock) and block.id == tool_call_id:
                block.state = state
                break

    def _save_to_context(
        self,
        blocks: Sequence[
            TextBlock
            | ThinkingBlock
            | ToolCallBlock
            | ToolResultBlock
            | DataBlock
        ],
        usage: ChatUsage | None = None,
    ) -> None:
        """Save content blocks into the context.

        Newly created :class:`AssistantMsg` uses ``self.state.reply_id`` as
        its id so that one reply corresponds to one message and the message
        id matches the ``reply_id`` carried by streaming events.
        """
        msg_usage = (
            Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )
            if usage is not None
            else None
        )

        # Assistant-produced audio (e.g. qwen-omni speaking aloud) is delivered
        # to the user via streaming events; the raw bytes don't belong in
        # conversation memory. Filtering here keeps every downstream walker
        # (formatter, count_tokens, persistence) honest without each having
        # to remember.
        persisted_blocks = [
            b
            for b in blocks
            if not (
                isinstance(b, DataBlock)
                and isinstance(b.source, (Base64Source, URLSource))
                and b.source.media_type.startswith("audio/")
            )
        ]
        if not persisted_blocks and msg_usage is None:
            return

        self.state.append_context(self.name, persisted_blocks)

        tail = self.state.context[-1]
        if msg_usage is not None:
            if tail.usage is None:
                tail.usage = msg_usage
            else:
                tail.usage.input_tokens += msg_usage.input_tokens
                tail.usage.output_tokens += msg_usage.output_tokens

    def _get_last_msg(self) -> Msg | None:
        """Get the last message in the context that belongs to this agent."""
        if len(self.state.context) == 0:
            return None
        last_msg = self.state.context[-1]
        if last_msg.role == "assistant" and last_msg.name == self.name:
            return last_msg
        return None

    def _next_action(
        self,
        final_msg: Msg | None = None,
    ) -> Reasoning | Acting | Exit:
        """Decide the next action from the current state. Read-only: all
        side effects are performed by the caller ``_reply_impl``."""

        # ===========================================================
        # Step 1: Check executable and awaiting tool calls
        # ===========================================================
        awaiting_tool_calls = self.state.get_awaiting_tool_calls(self.name)

        last_msg = self._get_last_msg()
        if last_msg is not None:
            # In case wrong tool call state, first filter with the results
            finished_ids = {
                _.id for _ in last_msg.get_content_blocks("tool_result")
            }
            # With awaiting tool calls, PENDING ones are blocked (e.g.
            # deduplicated in a batch) and wait; only ALLOWED ones execute
            executable_tool_calls = [
                _
                for _ in last_msg.get_content_blocks("tool_call")
                if _.id not in finished_ids
                and (
                    _.state == ToolCallState.ALLOWED
                    or (
                        _.state == ToolCallState.PENDING
                        and not awaiting_tool_calls
                    )
                )
            ]
            if executable_tool_calls:
                # Next execute the tool calls
                return Acting(tool_calls=executable_tool_calls)

        if awaiting_tool_calls:
            # Next wait for the permission or external execution to finish
            return Exit(
                # The reply doesn't finish yet
                exit_events=None,
                exit_msg=AssistantMsg(
                    id=self.state.reply_id,
                    name=self.name,
                    # both confirmation or external execution
                    content="I'm waiting for your permission or the "
                    "external execution to finish.",
                ),
            )

        # ===========================================================
        # Step 2: Check structured output if no blocked tool calls
        # ===========================================================

        # Structured output requirement and satisfaction
        required = self.state.reply_context.structured_schema is not None
        satisfied = self.state.reply_context.structured_output is not None

        if required and satisfied:
            # Next return the structured output and finish the reply
            return Exit(
                exit_events=[
                    ReplyEndEvent(
                        session_id=self.state.session_id,
                        reply_id=self.state.reply_id,
                        finished_reason=ReplyFinishedReason.COMPLETED,
                    ),
                ],
                exit_msg=AssistantMsg(
                    id=self.state.reply_id,
                    name=self.name,
                    content="The required structured output is generated.",
                    finished_reason=ReplyFinishedReason.COMPLETED,
                    structured_output=deepcopy(
                        self.state.reply_context.structured_output,
                    ),
                ),
            )

        elif required and not satisfied:
            # Maybe the model needs futher reasoning-acting to
            # generate the structured output
            tool_choice = None
            suffix = (
                "Call it when you are ready to generate the final "
                "structured output."
            )

            # Allow extra grace iterations for structured generation
            if (
                self.state.cur_iter
                >= self.react_config.max_iters
                + self.react_config.structured_output_grace_iters
            ):
                return Exit(
                    exit_events=[
                        ExceedMaxItersEvent(
                            reply_id=self.state.reply_id,
                            name=self.name,
                        ),
                        ReplyEndEvent(
                            session_id=self.state.session_id,
                            reply_id=self.state.reply_id,
                            finished_reason=(
                                ReplyFinishedReason.EXCEED_MAX_ITERS
                            ),
                        ),
                    ],
                    exit_msg=AssistantMsg(
                        id=self.state.reply_id,
                        name=self.name,
                        content="The maximum reasoning-acting iterations "
                        "are exceeded.",
                        finished_reason=ReplyFinishedReason.EXCEED_MAX_ITERS,
                    ),
                )

            if self.state.cur_iter >= self.react_config.max_iters:
                # Must call the structured output tool and return the
                # structured output
                tool_choice = ToolChoice(
                    mode=_GenerateStructuredOutput.name,
                )
                suffix = (
                    "You have reached the maximum reasoning-acting "
                    "iterations, so call this tool at once to generate "
                    "the final structured output."
                )

            # Next continue reasoning with injected hint
            return Reasoning(
                hint=HintBlock(
                    hint=[
                        TextBlock(
                            text=(
                                "<system-reminder>You're required to "
                                "generate structured output by calling the "
                                f"'{_GenerateStructuredOutput.name}' tool. "
                                f"{suffix}</system-reminder>"
                            ),
                        ),
                    ],
                    source='{"label": "System", "sublabel": '
                    '"Structured Output Requirement"}',
                ),
                tool_choice=tool_choice,
            )

        # ===========================================================
        # Step 3: Exit with the final message, or continue reasoning
        # ===========================================================

        # The last reasoning produced a text-only final message
        if final_msg is not None:
            return Exit(
                exit_events=[
                    ReplyEndEvent(
                        session_id=self.state.session_id,
                        reply_id=self.state.reply_id,
                        finished_reason=ReplyFinishedReason.COMPLETED,
                    ),
                ],
                exit_msg=final_msg,
            )

        if self.state.cur_iter >= self.react_config.max_iters:
            logger.warning(
                "Agent %s exceeds the max iteration numbers %d. "
                "Stop the react loop.",
                self.name,
                self.react_config.max_iters,
            )

            return Exit(
                exit_events=[
                    ExceedMaxItersEvent(
                        reply_id=self.state.reply_id,
                        name=self.name,
                    ),
                    ReplyEndEvent(
                        session_id=self.state.session_id,
                        reply_id=self.state.reply_id,
                        finished_reason=ReplyFinishedReason.EXCEED_MAX_ITERS,
                    ),
                ],
                exit_msg=AssistantMsg(
                    id=self.state.reply_id,
                    name=self.name,
                    content="The maximum reasoning-acting iterations are "
                    "exceeded.",
                    finished_reason=ReplyFinishedReason.EXCEED_MAX_ITERS,
                ),
            )

        # By default, continue reasoning
        return Reasoning()

    async def _convert_chat_response_to_event(
        self,
        block_ids: dict,
        chunk: ChatResponse,
    ) -> AsyncGenerator:
        """Convert a ChatResponse chunk into a sequence of agent events. To
        keep the identifiers of the content blocks reasonable, the input
        blocks_ids is used to track the block ids.

        Args:
            block_ids (`dict`):
                The block ids used to track the block generation.
            chunk (`ChatResponse`):
                The chat response chunk to be converted.
        """

        # Classify the content blocks into different types
        text_blocks, thinking_blocks, tool_call_blocks = [], [], []
        data_blocks: list = []
        for block in chunk.content:
            if isinstance(block, TextBlock):
                text_blocks.append(block)
            elif isinstance(block, ThinkingBlock):
                thinking_blocks.append(block)
            elif isinstance(block, ToolCallBlock):
                tool_call_blocks.append(block)
            elif isinstance(block, DataBlock):
                data_blocks.append(block)

        # Handle the thinking stream: continue/open or close.
        # We only auto-close when the chunk also carries no data blocks;
        # a data-only chunk (e.g. an omni-style audio PCM delta) must keep
        # both text and thinking streams alive so the frontend doesn't
        # fragment one logical stream into many separate bubbles.
        if thinking_blocks:
            # Generate a new thinking block id and start event
            if not block_ids.get("thinking"):
                block_ids["thinking"] = _generate_id()
                yield ThinkingBlockStartEvent(
                    reply_id=self.state.reply_id,
                    block_id=block_ids["thinking"],
                )
            # Generate the thinking delta event with the existing id
            yield ThinkingBlockDeltaEvent(
                reply_id=self.state.reply_id,
                block_id=block_ids["thinking"],
                delta="".join([_.thinking for _ in thinking_blocks]),
            )

        elif block_ids.get("thinking") and not data_blocks:
            yield ThinkingBlockEndEvent(
                reply_id=self.state.reply_id,
                block_id=block_ids["thinking"],
            )
            block_ids["thinking"] = None

        # Handle the text stream: continue/open or close.  Placed after
        # thinking so that a chunk carrying both ThinkingBlock and TextBlock
        # emits thinking events first.
        if text_blocks:
            if not block_ids.get("text"):
                block_ids["text"] = _generate_id()
                yield TextBlockStartEvent(
                    reply_id=self.state.reply_id,
                    block_id=block_ids["text"],
                )
            yield TextBlockDeltaEvent(
                reply_id=self.state.reply_id,
                block_id=block_ids["text"],
                delta="".join([_.text for _ in text_blocks]),
            )
        elif block_ids.get("text") and not data_blocks:
            yield TextBlockEndEvent(
                reply_id=self.state.reply_id,
                block_id=block_ids["text"],
            )
            block_ids["text"] = None

        # Handle the tool calls that exist in the current chunk
        for tool_call in tool_call_blocks:
            # Not in previous chunk, start with a start event
            if tool_call.id not in block_ids["tools"]:
                block_ids["tools"].append(tool_call.id)
                yield ToolCallStartEvent(
                    reply_id=self.state.reply_id,
                    tool_call_id=tool_call.id,
                    tool_call_name=tool_call.name,
                )
            yield ToolCallDeltaEvent(
                reply_id=self.state.reply_id,
                tool_call_id=tool_call.id,
                delta=tool_call.input,
            )

        # Handle the tool calls that exist in the previous chunk but not in the
        # current chunk
        finished_ids = set(block_ids["tools"]) - set(
            _.id for _ in tool_call_blocks
        )
        for finished_id in finished_ids:
            yield ToolCallEndEvent(
                reply_id=self.state.reply_id,
                tool_call_id=finished_id,
            )
            block_ids["tools"].remove(finished_id)

        # Handle the data blocks (streaming binary content, e.g. omni audio).
        # Each DataBlock chunk from the model carries a delta payload with a
        # stable block id; we open a stream the first time we see an id and
        # emit delta events for subsequent chunks with the same id.
        for data_block in data_blocks:
            if not isinstance(data_block.source, Base64Source):
                # Only Base64Source carries inline delta bytes; URLSource is
                # one-shot and not part of the streaming protocol.
                continue
            if data_block.id not in block_ids["data"]:
                block_ids["data"].append(data_block.id)
                yield DataBlockStartEvent(
                    reply_id=self.state.reply_id,
                    block_id=data_block.id,
                    media_type=data_block.source.media_type,
                )
            yield DataBlockDeltaEvent(
                reply_id=self.state.reply_id,
                block_id=data_block.id,
                data=data_block.source.data,
                media_type=data_block.source.media_type,
            )

    async def _convert_tool_chunk_to_event(
        self,
        tool_call_id: str,
        output_blocks: str | List[TextBlock | DataBlock],
    ) -> AsyncGenerator:
        """Convert a ToolChunk into a sequence of agent events."""
        if isinstance(output_blocks, str):
            yield ToolResultTextDeltaEvent(
                reply_id=self.state.reply_id,
                tool_call_id=tool_call_id,
                delta=output_blocks,
            )
            return

        for block in output_blocks:
            if isinstance(block, TextBlock):
                yield ToolResultTextDeltaEvent(
                    reply_id=self.state.reply_id,
                    tool_call_id=tool_call_id,
                    delta=block.text,
                )

            elif isinstance(block, DataBlock):
                if isinstance(block.source, Base64Source):
                    yield ToolResultDataDeltaEvent(
                        reply_id=self.state.reply_id,
                        tool_call_id=tool_call_id,
                        media_type=block.source.media_type,
                        data=block.source.data,
                    )
                elif isinstance(block.source, URLSource):
                    yield ToolResultDataDeltaEvent(
                        reply_id=self.state.reply_id,
                        tool_call_id=tool_call_id,
                        media_type=block.source.media_type,
                        url=str(block.source.url),
                    )
