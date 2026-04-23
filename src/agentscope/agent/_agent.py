# -*- coding: utf-8 -*-
"""The unified agent class in AgentScope library."""
import asyncio
import uuid
from asyncio import Queue
from copy import deepcopy
from typing import Any, AsyncGenerator, Sequence

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    ValidationInfo,
    SerializeAsAny,
    PrivateAttr,
    ConfigDict,
)

from ._state import AgentState
from ._utils import _ToolCallBatch
from .._logging import logger
from ..event import (
    AgentEvent,
    ModelCallEndedEvent,
    ModelCallStartedEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultBinaryDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    RequireUserConfirmEvent,
    RequireExternalExecutionEvent,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    BinaryBlockStartEvent,
    BinaryBlockDeltaEvent,
    BinaryBlockEndEvent,
    ExceedMaxItersEvent,
)
from ..model import (
    ChatResponse,
    ChatUsage,
    ChatModelBase,
    _deserialize_model,
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


class SummarySchema(BaseModel):
    """The compressed memory model, used to generate summary of old memories"""

    task_overview: str = Field(
        max_length=300,
        description=(
            "The user's core request and success criteria.\n"
            "Any clarifications or constraints they specified"
        ),
    )
    current_state: str = Field(
        max_length=300,
        description=(
            "What has been completed so far.\n"
            "File created, modified, or analyzed (with paths if relevant).\n"
            "Key outputs or artifacts produced."
        ),
    )
    important_discoveries: str = Field(
        max_length=300,
        description=(
            "Technical constraints or requirements uncovered.\n"
            "Decisions made and their rationale.\n"
            "Errors encountered and how they were resolved.\n"
            "What approaches were tried that didn't work (and why)"
        ),
    )
    next_steps: str = Field(
        max_length=200,
        description=(
            "Specific actions needed to complete the task.\n"
            "Any blockers or open questions to resolve.\n"
            "Priority order if multiple steps remain"
        ),
    )
    context_to_preserve: str = Field(
        max_length=300,
        description=(
            "User preferences or style requirements.\n"
            "Domain-specific details that aren't obvious.\n"
            "Any promises made to the user"
        ),
    )


class CompressionConfig(BaseModel):
    """The compression related configuration in AgentScope"""

    model_config = {"arbitrary_types_allowed": True}
    """Allow arbitrary types in the pydantic model."""

    trigger_threshold: int = 20000
    """The token threshold to trigger the compression process. When the
    total token count in the memory exceeds this threshold, the
    compression will be activated."""

    keep_recent: int = 5
    """The number of most recent messages to keep uncompressed in the
    memory to preserve the recent context."""

    compression_prompt: str = (
        "<system-hint>You have been working on the task described above "
        "but have not yet completed it. "
        "Now write a continuation summary that will allow you to resume "
        "work efficiently in a future context window where the "
        "conversation history will be replaced with this summary. "
        "Your summary should be structured, concise, and actionable."
        "</system-hint>"
    )
    """The prompt used to guide the compression model to generate the
    compressed summary, which will be wrapped into a user message and
    attach to the end of the current memory."""

    summary_template: str = (
        "<system-info>Here is a summary of your previous work\n"
        "# Task Overview\n"
        "{task_overview}\n\n"
        "# Current State\n"
        "{current_state}\n\n"
        "# Important Discoveries\n"
        "{important_discoveries}\n\n"
        "# Next Steps\n"
        "{next_steps}\n\n"
        "# Context to Preserve\n"
        "{context_to_preserve}"
        "</system-info>"
    )
    """The string template to present the compressed summary to the agent,
    which will be formatted with the fields from the
    `compression_summary_model`."""

    summary_schema: dict = Field(
        default_factory=SummarySchema.model_json_schema,
    )
    """The structured model used to guide the agent to generate the
    structured compressed summary."""

    compression_model: SerializeAsAny[ChatModelBase] | None = None
    """The compression model used to generate the compressed summary. If
    not provided, the agent's model will be used."""

    @field_validator("compression_model", mode="before")
    @classmethod
    def validate_compression_model(cls, v: Any, info: ValidationInfo) -> Any:
        """Deserialize compression_model from dict using context-injected
        custom classes."""
        if not isinstance(v, dict):
            return v
        custom_classes = (
            info.context.get("custom_model_classes", [])
            if info.context
            else []
        )
        return _deserialize_model(
            v,
            custom_classes=custom_classes,
            context=info.context,
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

    @field_validator("model", "fallback_model", mode="before")
    @classmethod
    def validate_model(cls, v: Any, info: ValidationInfo) -> Any:
        """Deserialize model from dict using context-injected custom
        classes."""
        if not isinstance(v, dict):
            return v
        custom_classes = (
            info.context.get("custom_model_classes", [])
            if info.context
            else []
        )
        return _deserialize_model(
            v,
            custom_classes=custom_classes,
            context=info.context,
        )

    def model_post_init(self, __context: Any) -> None:
        """Initialize the permission engine after the model is initialized."""
        self._engine = PermissionEngine(self.state.permission_context)

    # =======================================================================
    # Agent public methods
    # =======================================================================

    async def reply_stream(
        self,
        msgs: Msg | list[Msg] | AgentEvent | None = None,
        event: AgentEvent | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Reply to the given message and stream agent events."""
        try:
            async for chunk in self._reply(msgs=msgs, event=event):
                if not isinstance(chunk, Msg):
                    yield chunk
        finally:
            pass

    async def reply(
        self,
        msgs: Msg | list[Msg] | None = None,
        event: AgentEvent | None = None,
    ) -> Msg:
        """Reply to the given message, consuming all streamed events."""
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
        """Core reply logic. Yields chunks with is_last flag."""
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

            yield RunStartedEvent(
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
            # Step 3.1: Checking if exists tool calls to be executed
            # ===============================================================
            tool_calls = self._get_pending_tool_calls()

            # ===============================================================
            # Step 3.2: Execute reasoning if no more tools to be executed
            # ===============================================================
            if len(tool_calls) == 0:
                # Compressed the memory if needed before reasoning
                await self.compress_memory()
                # Perform reasoning
                async for evt in self._reasoning():
                    yield evt
                    # Exit the loop when no tool calls generated and the reply
                    # message is generated
                    if isinstance(evt, Msg):
                        yield RunFinishedEvent(
                            session_id=self.state.session_id,
                            reply_id=self.state.reply_id,
                        )
                        return

            # ===============================================================
            # Step 3.3: Getting batches of tool calls to be executed
            #  - If not, finish the loop by yielding RunFinishedEvent and exit
            #  - Otherwise, execute by batch and continue the loop
            # ===============================================================
            for batch in await self._batch_tool_calls():
                if batch.type == "sequential":
                    evt_generator = self._acting_tool_calls_sequential(
                        batch.tool_calls,
                    )

                elif batch.type == "concurrent":
                    evt_generator = self._acting_tool_calls_concurrent(
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
        ModelCallStartedEvent
        | TextBlockStartEvent
        | TextBlockDeltaEvent
        | TextBlockEndEvent
        | ToolCallBlock
        | ToolCallDeltaEvent
        | ToolCallEndEvent
        | ThinkingBlockStartEvent
        | ThinkingBlockDeltaEvent
        | ThinkingBlockEndEvent
        | BinaryBlockStartEvent
        | BinaryBlockDeltaEvent
        | BinaryBlockEndEvent
        | ModelCallEndedEvent
        | Msg,
        None,
    ]:
        """Core reasoning logic. Yields chunks with is_last flag."""
        # TODO: Pass tool schemas from toolkit when toolkit is implemented

        yield ModelCallStartedEvent(
            reply_id=self.state.reply_id,
            model_name=self.model.model_name,
        )

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
        tools = self.toolkit.get_function_schemas(self.state.activated_groups)

        res = await self._call_model(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
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
        yield ModelCallEndedEvent(
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

        if (
            self.state.context
            and self.state.context[-1].role == "assistant"
            and self.state.context[-1].name == self.name
        ):
            last_msg = self.state.context[-1]

            # The completed tool call ids
            tool_result_ids = [
                _.id for _ in last_msg.get_content_blocks("tool_result")
            ]

            for tool_call in last_msg.get_content_blocks("tool_call"):
                if tool_call.state == "ask":
                    awaiting_confirmations.append(tool_call.id)
                elif (
                    tool_call.state == "submitted"
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

            extra_ids = set(
                _.tool_call_id for _ in event.execution_results
            ) - set(
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
        | ToolResultBinaryDeltaEvent
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
            | ToolResultBinaryDeltaEvent \
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
                        tool_call.state = "allow"

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
                        tool_call.state = "deny"
                        async for evt in self._handle_denied_tool_call(
                            tool_call,
                            message=(
                                "<system-reminder>The execution of tool "
                                f'"{tool_call.name}" is denied by user!'
                                "</system-reminder>"
                            ),
                        ):
                            yield evt

                    # Delete for quick lookup and later processing
                    confirmed_tool_calls.pop(tool_call.id)

        elif isinstance(event, ExternalExecutionResultEvent):
            # Directly append the execution results into context
            self._save_to_context(event.execution_results)

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
                copied_msgs = [msgs]
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
        tool_calls = self._get_pending_tool_calls()

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
        | ToolResultBinaryDeltaEvent
        | ToolResultEndEvent,
        None,
    ]:
        """Execute the given tool calls sequentially and yield the events.

        Args:
            tool_calls (`list[ToolCallBlock]`):
                The tool calls to be executed sequentially.

        Yields:
            `RequireUserConfirmEvent \
            | RequireExternalExecutionEvent \
            | ToolResultStartEvent \
            | ToolResultTextDeltaEvent \
            | ToolResultBinaryDeltaEvent \
            | ToolResultEndEvent`:
                The events generated during the execution of the tool calls.
        """

        for tool_call in tool_calls:
            async for evt in self._execute_tool_call(tool_call):
                yield evt

    async def _execute_concurrent_tool_calls(
        self,
        tool_calls: list[ToolCallBlock],
    ) -> AsyncGenerator[
        RequireUserConfirmEvent
        | RequireExternalExecutionEvent
        | ToolResultStartEvent
        | ToolResultTextDeltaEvent
        | ToolResultBinaryDeltaEvent
        | ToolResultEndEvent,
        None,
    ]:
        """Execute the given tool calls concurrently and yield the events.

        Args:
            tool_calls (`list[ToolCallBlock]`):
                The tool calls to be executed concurrently.

        Yields:
            `RequireUserConfirmEvent \
            | RequireExternalExecutionEvent \
            | ToolResultStartEvent \
            | ToolResultTextDeltaEvent \
            | ToolResultBinaryDeltaEvent \
            | ToolResultEndEvent`:
                The events generated during the execution of the tool calls.
        """
        # Create a queue to gather the events from concurrent execution
        queue: Queue = Queue()

        # Start the concurrent execution tasks
        tasks = []
        for tool_call in tool_calls:
            tasks.append(
                asyncio.create_task(
                    self._into_queue(tool_call, queue),
                ),
            )

        # Await for the events from the queue and yield
        while True:
            if len(tasks) == 0 and queue.empty():
                break

            event = await queue.get()
            yield event

            # Check if any task is completed
            done_tasks = [task for task in tasks if task.done()]
            for done in done_tasks:
                tasks.remove(done)

    async def _into_queue(
        self,
        tool_call: ToolCallBlock,
        queue: Queue,
    ) -> None:
        """Convert the yield events from tool call execution into queue put."""
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
        | ToolResultBinaryDeltaEvent
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
            | ToolResultBinaryDeltaEvent \
            | ToolResultEndEvent`:
                The events generated during the tool call execution.
        """
        # ===================================================================
        # Step 1: Check permission by toolkit and permission engine
        #  - Treat unregistered tools as allowed and handle the permission
        #   within the toolkit execution.
        # ===================================================================
        registered_tool = self.toolkit.tools.get(tool_call.name)
        if registered_tool is None:
            # For unexisting tool, treat it as allowed and handled it within
            # the toolkit execution
            decision = PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                decision_reason="Unregistered tool, allow by default",
                message="Unregistered tool, allow by default",
            )

        else:
            decision = await self._engine.check_permission(
                tool_call,
                registered_tool.tool,
            )

        # ===================================================================
        # Step 2: Handle the permission and execute the tool call if allowed
        # ===================================================================

        # Case 1: Ask for user confirmation if needed
        if decision.behavior in [
            PermissionBehavior.ASK,
            PermissionBehavior.PASSTHROUGH,
        ]:
            yield RequireUserConfirmEvent(
                reply_id=self.state.reply_id,
                tool_calls=[tool_call],
            )
            return

        # Case 2: Denied by the permission system
        if decision.behavior == PermissionBehavior.DENY:
            async for evt in self._handle_denied_tool_call(
                tool_call,
                decision.message,
            ):
                yield evt
            return

        # Case 3: Allowed by the permission system, execute the tool call and
        #  yield the events
        if decision.behavior == PermissionBehavior.ALLOW:
            # Send start event
            yield ToolResultStartEvent(
                reply_id=self.state.reply_id,
                tool_call_id=tool_call.id,
                tool_call_name=tool_call.name,
            )
            # TODO: 检查外部执行，并且抛出对应的事件
            if self.toolkit.require_external_execution(tool_call):
                yield RequireExternalExecutionEvent(
                    reply_id=self.state.reply_id,
                    tool_calls=[tool_call],
                )
                return

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
                    # The ended event for the tool result
                    yield ToolResultEndEvent(
                        reply_id=self.state.reply_id,
                        tool_call_id=tool_call.id,
                        state=chunk.state,
                    )
                else:
                    async for evt in self._convert_tool_chunk_to_event(
                        tool_call,
                        chunk,
                    ):
                        yield evt

            return

        raise ValueError(
            f"Invalid permission decision behavior: {decision.behavior}",
        )

    async def _handle_denied_tool_call(
        self,
        tool_call: ToolCallBlock,
        message: str,
    ) -> AsyncGenerator[
        ToolResultStartEvent
        | ToolResultTextDeltaEvent
        | ToolResultBinaryDeltaEvent
        | ToolResultEndEvent,
        None,
    ]:
        """Handle the denied tool call and generate the corresponding events.

        Yields:
            `ToolResultStartEvent \
            | ToolResultTextDeltaEvent \
            | ToolResultBinaryDeltaEvent \
            | ToolResultEndEvent`:
                The events generated for the denied tool call.
        """

        yield ToolResultStartEvent(
            reply_id=self.state.reply_id,
            tool_call_id=tool_call.id,
            tool_call_name=tool_call.name,
        )

        result = ToolChunk(
            content=[TextBlock(text=message)],
            state="error",
        )

        # Return the result directly to the agent
        self._save_to_context(
            [
                ToolResultBlock(
                    id=tool_call.id,
                    name=tool_call.name,
                    output=message,
                    state="error",
                ),
            ],
        )

        async for evt in self._convert_tool_chunk_to_event(tool_call, result):
            yield evt

        yield ToolResultEndEvent(
            reply_id=self.state.reply_id,
            tool_call_id=tool_call.id,
            state="error",
        )

    # =======================================================================
    # Context management related methods
    # =======================================================================

    def _split_context_for_compression(self) -> tuple[list[Msg], list[Msg]]:
        """Split context into parts to compress and parts to keep recent."""
        # TODO: Implement full splitting logic when compression_config is
        #  available
        return [], list(self.state.context)

    async def _compress_memory_if_needed(self) -> None:
        """Compress the agent's memory if the token count exceeds the
        threshold."""
        # TODO: Implement when compression_config is available

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
                    return await self.model(
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

    def _get_pending_tool_calls(self) -> list[ToolCallBlock]:
        """Get tool calls from the last assistant message that have no result
        yet."""
        if len(self.state.context) == 0:
            return []
        last_msg = self.state.context[-1]
        if last_msg.role != "assistant" and last_msg.name != self.name:
            return []

        # The tool results
        result_ids = {_.id for _ in last_msg.get_content_blocks("tool_result")}
        # Return the tool calls that doesn't have results yet
        return [
            _
            for _ in last_msg.get_content_blocks("tool_call")
            if _.id not in result_ids
        ]

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
        tool_call: ToolCallBlock,
        chunk: ToolChunk,
    ) -> AsyncGenerator:
        """Convert a ToolChunk into a sequence of agent events."""
        for block in chunk.content:
            if isinstance(block, TextBlock):
                yield ToolResultTextDeltaEvent(
                    reply_id=self.state.reply_id,
                    tool_call_id=tool_call.id,
                    delta=block.text,
                )

            elif isinstance(block, DataBlock):
                if isinstance(block.source, Base64Source):
                    yield ToolResultBinaryDeltaEvent(
                        reply_id=self.state.reply_id,
                        tool_call_id=tool_call.id,
                        media_type=block.source.media_type,
                        data=block.source.data,
                    )
                elif isinstance(block.source, URLSource):
                    yield ToolResultBinaryDeltaEvent(
                        reply_id=self.state.reply_id,
                        tool_call_id=tool_call.id,
                        media_type=block.source.media_type,
                        url=str(block.source.url),
                    )
