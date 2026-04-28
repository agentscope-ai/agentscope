# -*- coding: utf-8 -*-
"""The unified agent class in AgentScope library."""
import asyncio
import uuid
from asyncio import Queue
from copy import deepcopy
from typing import Any, AsyncGenerator, Sequence, Literal, List

import jsonschema
from pydantic import (
    BaseModel,
    Field,
    SerializeAsAny,
    PrivateAttr,
    ConfigDict,
)

from ._config import CompressionConfig
from ._state import AgentState
from ._utils import _ToolCallBatch
from .._logging import logger
from .._utils._common import _json_loads_with_repair
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
)
from ..exception import AgentOrientedException
from ..model import (
    ChatResponse,
    ChatUsage,
    ChatModelBase,
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
)
from ..tool import (
    Toolkit,
    ToolChunk,
    ToolChoice,
    PermissionBehavior,
    ToolResponse,
    PermissionEngine,
    PermissionDecision,
)


class ReasoningConfig(BaseModel):
    """The reasoning related configuration"""

    max_iters: int = 20
    """The maximum number of iterations for the reasoning-acting loop."""


class ActingConfig(BaseModel):
    """The acting related configuration in AgentScope"""

    parallel: bool = True
    """Whether to execute tool calls in parallel when there are multiple tool
    calls awaiting execution."""


class Agent(BaseModel):
    """The agent class."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(description="The identifier of the agent.")
    """The name of the agent."""

    system_prompt: str = Field(default="You're a helpful assistant.")
    """The base system prompt of the agent, extra hints will be attached to
    this prompt during agent's reply."""

    model: SerializeAsAny[ChatModelBase] = Field(
        description="The language model used by the agent.",
    )
    """The language model used by the agent."""

    max_retries: int = Field(
        default=10,
        gt=0,
        description="Maximum number of retries when the model call fails.",
    )
    """The maximum number of retries when the model call fails. Must be
    greater than 0."""

    fallback_model: SerializeAsAny[ChatModelBase] | None = Field(
        default=None,
        description="The fallback model used when the main model fails.",
    )
    """The fallback model used when the main model fails. Also supports the
    max_retries logic."""

    compression: CompressionConfig = Field(
        default_factory=CompressionConfig,
        description="The compression related configuration for the agent.",
    )
    """The agent compression related configuration."""

    reasoning: ReasoningConfig = Field(
        default_factory=ReasoningConfig,
        description="The reasoning related configuration for the agent.",
    )
    """The reasoning related configuration for the agent."""

    acting: ActingConfig = Field(
        default_factory=ActingConfig,
        description="The acting related configuration for the agent.",
    )
    """The acting, i.e. tool execution, related configuration for the agent."""

    state: AgentState = Field(default_factory=AgentState)
    """The agent state, including the conversation context, permission context,
    tool context, etc."""

    toolkit: Toolkit = Field(exclude=True)
    """The toolkit used by the agent."""

    _engine: PermissionEngine = PrivateAttr()
    """The permission engine used to manage the tool usage permissions for the
    agent."""

    # @field_validator("model", "fallback_model", mode="before")
    # @classmethod
    # def validate_model(cls, v: Any, info: ValidationInfo) -> Any:
    #     """Deserialize model from dict using context-injected custom
    #     classes."""
    #     if not isinstance(v, dict):
    #         return v
    #     custom_classes = (
    #         info.context.get("custom_model_classes", [])
    #         if info.context
    #         else []
    #     )
    #     return _deserialize_model(
    #         v,
    #         custom_classes=custom_classes,
    #         context=info.context,
    #     )

    def model_post_init(self, __context: Any) -> None:
        """Initialize the permission engine after the model is initialized."""
        self._engine = PermissionEngine(self.state.permission_context)

    # =======================================================================
    # Agent public methods
    # =======================================================================

    async def reply_stream(
        self,
        msgs: Msg | list[Msg] | None = None,
        event: UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Reply to the given message and stream agent events.


        **NOTE**:

        - If requiring outside interaction for multiple tool calls and only
         receive partial confirmation or execution results, the agent won't
         re-send the requiring events for the unconfirmed or unexecuted tool
         calls.
        """
        try:
            async for chunk in self._reply(msgs=msgs, event=event):
                if not isinstance(chunk, Msg):
                    yield chunk
        finally:
            pass

    async def reply(
        self,
        msgs: Msg | list[Msg] | None = None,
        event: UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> Msg:
        """Reply to the given message, consuming all streamed events.

        Args:
            msgs (`Msg | list[Msg] | None`, optional):
                The message(s) to reply to. Can be a single `Msg` object,
                a list of `Msg` objects, or `None` if there are no new
                messages.
            event (`UserConfirmResultEvent | ExternalExecutionResultEvent | \
            None`, optional):
                The event to continue from, which should be the result of the
                required outside interaction triggered by the previous reply.
                If the previous reply does not trigger any outside
                interaction, this should be `None`.

        Returns:
            `Msg`:
                A final reply message.
        """
        try:
            final_msg: Msg | None = None
            async for evt_or_msg in self._reply(msgs=msgs, event=event):
                if isinstance(evt_or_msg, Msg):
                    final_msg = evt_or_msg
            if final_msg is None:
                raise RuntimeError("Agent did not produce a final message.")
            return final_msg
        finally:
            pass

    async def observe(self, msgs: Msg | list[Msg] | None = None) -> None:
        """Receive external observation message(s) and save them into
        context."""
        await self._handle_incoming_messages(msgs)

    async def compress_context(
        self,
        compression_config: CompressionConfig | None = None,
    ) -> None:
        """Compress the agent's context if the token count exceeds the
        threshold.

        Args:
            compression_config (`CompressionConfig | None`, optional):
                If provided, compress the context with the given compression
                config. Otherwise, use the default compression config in the
                agent.
        """
        cfg: CompressionConfig = compression_config or self.compression

        # Count the current tokens
        kwargs = await self._prepare_model_input()
        estimated_tokens = await self.model.count_tokens(**kwargs)

        # Skip if no compression is needed
        threshold = cfg.trigger_ratio * self.model.context_length
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
            cfg.reserve_ratio * self.model.context_length,
            tools,
        )

        # Prepare the messages to compress
        msgs_system = [
            SystemMsg(
                name="system",
                content=await self._get_system_prompt(),
            ),
        ]
        if self.state.summary:
            msgs_system.append(UserMsg("user", self.state.summary))

        messages = (
            msgs_system
            + msgs_to_compress
            + [
                UserMsg(name="user", content=cfg.compression_prompt),
            ]
        )

        # Compress the messages
        model = cfg.compression_model or self.model
        res = await model.generate_structured_output(
            messages=messages,
            structured_model=cfg.summary_schema,
        )

        # Update the summary
        self.state.summary = cfg.summary_template.format(**res.content)
        # Update the context
        self.state.context = msgs_to_reserve

        logger.info(
            "[AGENT %s]: The context compression finished.",
            self.name,
        )

    # ======================================================================
    # Agent core methods, including _reply, _reasoning, _acting, etc.
    # ======================================================================

    async def _reply(
        self,
        msgs: Msg | list[Msg] | None = None,
        event: UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Core reply logic."""
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
            self.state.reply_id = uuid.uuid4().hex
            self.state.cur_iter = 0

            yield ReplyStartEvent(
                session_id=self.state.session_id,
                reply_id=self.state.reply_id,
                name=self.name,
            )

        # ===================================================================
        # Step 3: Enter the reasoning-acting loop until reaching max_iters or
        #  no more tool calls to execute
        # ===================================================================
        while self.state.cur_iter < self.reasoning.max_iters:
            # ===============================================================
            # Step 3.1:
            # ===============================================================
            action, data = self._check_next_action()
            if action == "exit" and isinstance(data, Msg):
                yield data
                return

            # ===============================================================
            # Step 3.2: Execute reasoning if no more tools to be executed
            # ===============================================================
            if action == "reasoning":
                # Compressed the memory if needed before reasoning
                await self.compress_context()
                # Perform reasoning
                async for evt in self._reasoning():
                    # Exit the loop when no tool calls generated and the reply
                    # message is generated
                    if isinstance(evt, Msg):
                        yield ReplyEndEvent(
                            session_id=self.state.session_id,
                            reply_id=self.state.reply_id,
                        )
                        yield evt
                        return
                    yield evt

            # ===============================================================
            # Step 3.3: Getting batches of tool calls to be executed
            #  - If not, finish the loop by yielding RunFinishedEvent and exit
            #  - Otherwise, execute by batch and continue the loop
            # ===============================================================
            for batch in await self._batch_tool_calls():
                if batch.type == "sequential":
                    evt_generator = self._execute_sequential_tool_calls(
                        batch.tool_calls,
                    )

                elif batch.type == "concurrent":
                    evt_generator = self._execute_concurrent_tool_calls(
                        batch.tool_calls,
                    )

                else:
                    raise ValueError(
                        f"Invalid batch type: {batch.type}",
                    )

                break_execution = False
                async for evt in evt_generator:
                    yield evt
                    if isinstance(
                        evt,
                        (
                            RequireUserConfirmEvent,
                            RequireExternalExecutionEvent,
                        ),
                    ):
                        break_execution = True

                # If it requires outside interaction stop executing the next
                # batch and wait for outside trigger events
                if break_execution:
                    # Yield a Msg object for outside handling
                    yield AssistantMsg(
                        id=self.state.reply_id,
                        name=self.name,
                        content="Waiting for tool calls to be confirmed or "
                        "executed from outside ...",
                    )

                    return

            # Update the iteration count after each round of reasoning-acting
            self.state.cur_iter += 1

        # ===================================================================
        # Step 4: Handling the max iteration executed
        # ===================================================================
        yield ExceedMaxItersEvent(
            reply_id=self.state.reply_id,
            name=self.name,
        )

        yield AssistantMsg(
            id=self.state.reply_id,
            name=self.name,
            content="Executed maximum iterations of reasoning-acting loop"
            "without finishing the task.",
        )

    async def _reasoning(
        self,
        tool_choice: ToolChoice = "auto",
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
        # TODO: Pass tool schemas from toolkit when toolkit is implemented

        yield ModelCallStartEvent(
            reply_id=self.state.reply_id,
            model_name=self.model.model_name,
        )

        # Get the input arguments for the chat model, including messages and
        # tools
        kwargs = await self._prepare_model_input()

        # Call the chat model
        res = await self._call_model(
            tool_choice=tool_choice,
            **kwargs,
        )

        block_ids: dict = {"text": None, "thinking": None, "tools": []}
        completed_response: ChatResponse | None = None

        if self.model.stream:
            async for chunk in res:
                # Break if it's the last chunk with completed response
                if chunk.is_last:
                    completed_response = chunk
                    break

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

        # Send the model call ended event with usage if available
        yield ModelCallEndEvent(
            reply_id=self.state.reply_id,
            input_tokens=completed_response.usage.input_tokens
            if completed_response.usage
            else 0,
            output_tokens=completed_response.usage.output_tokens
            if completed_response.usage
            else 0,
        )

        self._save_to_context(
            list(completed_response.content),
            completed_response.usage,
        )

        # If no tool call is generated, return the final message directly
        if not any(
            isinstance(_, ToolCallBlock) for _ in completed_response.content
        ):
            yield AssistantMsg(
                id=self.state.reply_id,
                name=self.name,
                content=list(completed_response.content),
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
        awaiting_confirmations = []
        awaiting_external_executions = []

        last_msg = self._get_last_msg()
        if last_msg:
            # The completed tool call ids
            tool_result_ids = [
                _.id for _ in last_msg.get_content_blocks("tool_result")
            ]

            for tool_call in last_msg.get_content_blocks("tool_call"):
                if tool_call.state == ToolCallState.ASKING:
                    awaiting_confirmations.append(tool_call.id)
                elif (
                    tool_call.state == ToolCallState.SUBMITTED
                    and tool_call.id not in tool_result_ids
                ):
                    # submitted but no result yet, i.e. external execution
                    awaiting_external_executions.append(tool_call.id)

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

    async def _batch_tool_calls(self) -> list[_ToolCallBatch]:
        """Batch the tool calls into a sequence of batches that should be
        executed **sequentially** or **concurrently** according to the tool
        properties `is_concurrency_safe` and `is_read_only`.
        """
        # All tool calls that haven't the corresponding results in the context
        tool_calls = self._get_executable_tool_calls()

        # Batch the tool calls according to whether they can be executed
        # concurrently or not
        batches: list[_ToolCallBatch] = []
        for tool_call in tool_calls:
            registered_tool = self.toolkit.tools.get(tool_call.name)

            # Treat unregistered or unavailable tools as concurrent tools since
            # it will not generate side effects and be blocked with acting
            if (
                registered_tool is None
                or registered_tool.tool.is_concurrency_safe
            ):
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
                *[self._into_queue(tc, queue) for tc in tool_calls],
                return_exceptions=True,
            )
            # The sentinel is placed AFTER gather returns, which guarantees
            # that every queue.put inside _into_queue has already completed.
            await queue.put(sentinel)
            return results  # type: ignore[return-value]

        gather_task = asyncio.create_task(_run_all())

        # Drain the queue until the sentinel is encountered.
        while True:
            event = await queue.get()
            if event is sentinel:
                break
            yield event

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
    ) -> None:
        """Execute a single tool call and forward every event into *queue*.

        Args:
            tool_call (`ToolBlockCall`):
                The tool call to execute.
            queue (`Queue`):
                The shared async queue that collects events from all
                concurrent workers.
        """
        async for evt in self._execute_tool_call(tool_call):
            await queue.put(evt)

    async def _execute_tool_call(
        self,
        tool_call: ToolCallBlock,
    ) -> AsyncGenerator[
        RequireUserConfirmEvent
        | RequireExternalExecutionEvent
        | ToolResultStartEvent
        | ToolResultTextDeltaEvent
        | ToolResultDataDeltaEvent
        | ToolResultEndEvent,
        None,
    ]:
        """Execute a single tool call with permission checking.

        Args:
            tool_call (`ToolCallBlock`):
                The tool call block to be executed.

        Yields:
            `RequireUserConfirmEvent \
            | RequireExternalExecutionEvent \
            | ToolResultStartEvent \
            | ToolResult \
            | TextDeltaEvent \
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
            tool = self.toolkit.check_tool_available(
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
            # Set the state of the tool call to "ask"
            # **Note** the update must be done before yielding the event
            self._update_tool_call_state(
                tool_call.id,
                ToolCallState.ASKING,
            )

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

            # Execute the tool call and yield the events.

            res = self.toolkit.call_tool(tool_call, self.state)
            async for chunk in res:
                if isinstance(chunk, ToolResponse):
                    self._save_to_context(
                        [
                            ToolResultBlock(
                                id=tool_call.id,
                                name=tool_call.name,
                                output=chunk.content,
                                state=chunk.state,
                            ),
                        ],
                    )
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
                    )

                else:
                    async for evt in self._convert_tool_chunk_to_event(
                        tool_call.id,
                        chunk.content,
                    ):
                        yield evt

            return

        raise ValueError(
            f"Invalid permission decision behavior: {decision.behavior}",
        )

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
                The state of the tool result, which can be "error", "denied",

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

        # Adjust the block_index to avoid splitting tool call and result pairs

        # Check if the reserved part has tool results that don't have the
        # corresponding tool calls
        remain_result_ids = {}
        for i in range(len(boundary_msg_content) - 1, block_index, -1):
            block = boundary_msg_content[i]
            if isinstance(block, ToolResultBlock):
                remain_result_ids[block.id] = i
            elif isinstance(block, ToolCallBlock):
                remain_result_ids.pop(block.id, None)

        # Find the largest index of the remaining tool results, which doesn't
        # have the corresponding tool calls in the reserved parts
        if remain_result_ids:
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

    # ======================================================================
    # Agent internal utility methods
    # ======================================================================

    async def _get_system_prompt(self) -> str:
        """Get the system prompt of the agent."""
        prompt = [self.system_prompt]

        # Skill related instructions
        skill_instructions = await self.toolkit.get_skill_instructions()
        if skill_instructions:
            prompt.append(skill_instructions)

        return "\n".join(prompt)

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
        tools = self.toolkit.get_function_schemas(
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
        tool_choice: ToolChoice,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Perform model inference and return the response.

        Args:
            messages (`list[Msg]`):
                The input messages to the model.
            tools (`list[dict]`):
                The function schemas of the tools.
            tool_choice (`ToolChoice`):
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
        if self.fallback_model:
            models.append(self.fallback_model)

        last_exception = None
        for model in models:
            for _ in range(self.max_retries):
                try:
                    return await model(
                        messages=messages,
                        tools=tools,
                        tool_choice=tool_choice,
                    )
                except Exception as e:
                    logger.warning(
                        "Model %s call failed for agent %s. "
                        "Retrying (%d/%d)...",
                        model.model_name,
                        self.name,
                        _ + 1,
                        self.max_retries,
                    )
                    last_exception = e

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
        _usage: ChatUsage | None = None,
    ) -> None:
        """Save content blocks into the context."""
        if len(self.state.context) == 0:
            self.state.context.append(
                AssistantMsg(name=self.name, content=list(blocks)),
            )
        else:
            last_msg = self.state.context[-1]
            if last_msg.role == "assistant" and last_msg.name == self.name:
                if isinstance(last_msg.content, str):
                    last_msg.content = [TextBlock(text=last_msg.content)]
                last_msg.content.extend(blocks)
                # TODO: Merge usage if needed
            else:
                self.state.context.append(
                    AssistantMsg(
                        name=self.name,
                        content=list(blocks),
                    ),
                )

    def _get_last_msg(self) -> Msg | None:
        """Get the last message in the context that belongs to this agent."""
        if len(self.state.context) == 0:
            return None
        last_msg = self.state.context[-1]
        if last_msg.role == "assistant" and last_msg.name == self.name:
            return last_msg
        return None

    def _check_next_action(
        self,
    ) -> (
        tuple[Literal["exit"], Msg]
        | tuple[Literal["reasoning"], None]
        | tuple[Literal["acting"], None]
    ):
        """Check the next action for the agent

        Awaiting tool calls:
            The tool calls waiting for the outside events (confirmation or
            external execution results, state = "asking" or "submitted")
        Executable tool calls:
            The tool calls allowed by the incoming confirmation events and
            haven't been executed yet (state = "allowed")

        The next action:

        |                          | Awaiting tool calls          | No awaiting tool call        |
        | ------------------------ | ---------------------------- | ---------------------------- |
        | Executable tool calls    | Acting executable tool calls | Acting executable tool calls |
        | No executable tool calls | Exit the _reply              | Reasoning                    |

        Returns:
            `tuple[Literal["exit"], Msg]`:
                If there is no executable tool call and there are awaiting tool
                calls, which means the agent is waiting for the outside events
                and should not do anything before that, the next action is to
                exit the _reply and wait for the outside events.
            `tuple[Literal["reasoning"], None]`:
                If there is no executable tool call and no awaiting tool call,
                which means the agent has nothing to do in this iteration and
                can continue reasoning for the next step.
            `tuple[Literal["acting"], None]`:
                If there are executable tool calls, which means the agent can
                act by executing the tool calls.
        """  # noqa: E501
        last_msg = self._get_last_msg()
        if last_msg is None:
            return "reasoning", None

        # In case wrong tool call state, first filter with the results
        finished_ids = {
            _.id for _ in last_msg.get_content_blocks("tool_result")
        }
        unfinished_tool_calls = [
            _
            for _ in last_msg.get_content_blocks("tool_call")
            if _.id not in finished_ids
        ]

        # Find if there are executable or awaiting tool calls
        awaiting_tool_calls: list[ToolCallBlock] = []
        executable_tool_calls: list[ToolCallBlock] = []

        confirming_names, asking_names = [], []
        for _ in unfinished_tool_calls:
            if _.state in [ToolCallState.PENDING, ToolCallState.ALLOWED]:
                executable_tool_calls.append(_)

            elif _.state == ToolCallState.ASKING:
                asking_names.append(_.name)
                awaiting_tool_calls.append(_)

            elif _.state == ToolCallState.SUBMITTED:
                confirming_names.append(_.name)
                awaiting_tool_calls.append(_)

        if executable_tool_calls:
            return "acting", None

        if awaiting_tool_calls:
            # Prepare the message
            evt = ["I'm waiting for "]
            if asking_names:
                evt += [
                    f"user confirmation for {len(asking_names)} tool calls",
                ]

            if confirming_names:
                if evt:
                    evt += [", and "]
                evt += [
                    f"external execution results for {len(confirming_names)} "
                    f"tool calls",
                ]

            text = "".join(evt) + "."

            return "exit", AssistantMsg(
                name=self.name,
                content=[TextBlock(text=text)],
            )

        return "reasoning", None

    def _get_executable_tool_calls(self) -> list[ToolCallBlock]:
        """Get tool calls from the last message that to be executed, which
        means we should reserve the tool calls that:

        1. doesn't have results yet, **and**
        2. haven't been submitted for external execution (state != "submitted")
        """
        last_msg = self._get_last_msg()
        if last_msg is None:
            return []

        # The tool results
        result_ids = {_.id for _ in last_msg.get_content_blocks("tool_result")}
        # The tool calls that doesn't have results yet
        tool_calls_wo_results = [
            _
            for _ in last_msg.get_content_blocks("tool_call")
            if _.id not in result_ids
        ]

        # Filter the ones that are "submitted", which already report the
        # external execution requirement
        pending_tool_calls = [
            _
            for _ in tool_calls_wo_results
            if _.state
            in [
                ToolCallState.PENDING,
                ToolCallState.ALLOWED,
            ]
        ]
        return pending_tool_calls

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
        for block in chunk.content:
            if isinstance(block, TextBlock):
                text_blocks.append(block)
            elif isinstance(block, ThinkingBlock):
                thinking_blocks.append(block)
            elif isinstance(block, ToolCallBlock):
                tool_call_blocks.append(block)

        # Handle the text blocks
        if text_blocks:
            # If the current chunk has text blocks but no text block id,
            # start with a start event
            if not block_ids.get("text"):
                block_ids["text"] = uuid.uuid4().hex
                yield TextBlockStartEvent(
                    reply_id=self.state.reply_id,
                    block_id=block_ids["text"],
                )
            # Go on using the existing text block id to generate delta events
            yield TextBlockDeltaEvent(
                reply_id=self.state.reply_id,
                block_id=block_ids["text"],
                delta="".join([_.text for _ in text_blocks]),
            )

        elif block_ids.get("text"):
            yield TextBlockEndEvent(
                reply_id=self.state.reply_id,
                block_id=block_ids["text"],
            )
            block_ids["text"] = None

        # Handle the thinking blocks
        if thinking_blocks:
            # Generate a new thinking block id and start event
            if not block_ids.get("thinking"):
                block_ids["thinking"] = uuid.uuid4().hex
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

        elif block_ids.get("thinking"):
            yield ThinkingBlockEndEvent(
                reply_id=self.state.reply_id,
                block_id=block_ids["thinking"],
            )
            block_ids["thinking"] = None

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
