# -*- coding: utf-8 -*-
"""The unified agent class in AgentScope library."""
import uuid
from typing import Any, AsyncGenerator

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    ValidationInfo,
    SerializeAsAny,
)

from . import AgentState
from ._utils import _ToolCallBatch
from .. import logger
from .._utils._common import _json_loads_with_repair
from ..event import (
    AgentEvent,
    EventType,
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
)
from ..model import ChatResponse, ChatUsage, ChatModelBase, _deserialize_model
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

    toolkit: Toolkit = Field(
        default_factory=Toolkit,
        description="The toolkit related configuration for the agent.",
        exclude=True,
    )
    """The toolkit used by the agent."""

    state: AgentState

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
            async for chunk in self._reply(msgs=msgs, event=event):
                if chunk.is_last:
                    final_msg = chunk.msg
            if final_msg is None:
                raise RuntimeError("Agent did not produce a final message.")
            return final_msg
        finally:
            pass

    async def _get_system_prompt(self) -> str:
        """Get the system prompt of the agent."""
        prompt = [self.system_prompt]

        # Skill related instructions
        skill_instructions = self.toolkit.get_skill_instructions()
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
        blocks: list,
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
        if last_msg.role != "assistant":
            return []

        # The tool results
        result_ids = {
            _.id for _ in last_msg.get_content_blocks("tool_result")
        }
        # Return the tool calls that doesn't have results yet
        return [ _ for _ in last_msg.get_content_blocks("tool_call") if _.id not in result_ids]

    def _get_awaiting_tool_calls(self) -> dict:
        """Get the tool calls from the context that

        - haven't got results yet
        - group them into a sequence of batches according to whether they can
         be executed concurrently or not
        - get the first batch and find the tools requires user confirmation or
         external execution.
        """
        # Get the tools that haven't got results yet
        pending_tool_calls = self._get_pending_tool_calls()

        # Check the tool calls that requires user confirmation or external execution
        # The tool calls that should be executed before user confirmation or
        # external execution
        pre_tool_calls: list = []
        awaiting_tool_calls: list = []

        for index, tool_call in enumerate(pending_tool_calls):
            # First check the user confirmation
            if self.toolkit.require_user_confirmation(tool_call) and tool_call.await_user_confirmation:
                awaiting_tool_calls.append(tool_call)

                # Find the continuous tool calls that require user confirmation
                i = index + 1
                for i in range(i, len(pending_tool_calls)):
                    next_tool_call = pending_tool_calls[i]
                    # If it doesn't require user confirmation or already
                    # received user confirmation, break the loop
                    if not self.toolkit.require_user_confirmation(next_tool_call) or not next_tool_call.await_user_confirmation:
                        break
                    next_tool_call.await_user_confirmation = True

                return {
                    "awaiting_type": EventType.REQUIRE_USER_CONFIRM,
                    "expected_event_type": EventType.USER_CONFIRM_RESULT,
                    "awaiting_tool_calls": pending_tool_calls[index : i],
                    "pre_tool_calls": pre_tool_calls,
                }

            # If no user confirmation needed, check external execution
            if self.toolkit.require_external_execution(tool_call):
                awaiting_tool_calls.append(tool_call)

                i = index + 1
                for i in range(i, len(pending_tool_calls)):
                    next_tool_call = pending_tool_calls[i]
                    if not self.toolkit.require_external_execution(next_tool_call):
                        break

                return {
                    "awaiting_type": EventType.REQUIRE_EXTERNAL_EXECUTION,
                    "expected_event_type": EventType.EXTERNAL_EXECUTION_RESULT,
                    "awaiting_tool_calls": pending_tool_calls[index : i],
                    "pre_tool_calls": pre_tool_calls,
                }

            pre_tool_calls.append(tool_call)

        return {
            "awaiting_type": None,
            "expected_event_type": None,
            "awaiting_tool_calls": [],
            "pre_tool_calls": pre_tool_calls,
        }

    async def _reply(
        self,
        msgs: Msg | list[Msg] | None = None,
        event: AgentEvent | None = None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Core reply logic. Yields chunks with is_last flag."""
        awaiting_info = self._get_awaiting_tool_calls()
        expected_event_type = awaiting_info["expected_event_type"]

        if expected_event_type is not None:
            if event is None or event.type != expected_event_type:
                raise ValueError(
                    f"Agent is awaiting '{expected_event_type}' but received "
                    f"event of type '{event.type if event else 'none'}'.",
                )

            if isinstance(event, ExternalExecutionResultEvent):
                self._save_to_context(list(event.execution_results))

            elif isinstance(event, UserConfirmResultEvent):
                for result in event.confirm_results:
                    confirmed = result.confirmed
                    tool_call = result.tool_call
                    if confirmed:
                        self.state.confirmed_tool_call_ids.append(
                            tool_call["id"],
                        )
                    else:
                        rejection_text = (
                            f"<system-info>**Note** the user rejected the "
                            f"execution of tool "
                            f'"{tool_call["name"]}"!</system-info>'
                        )
                        yield ToolResultStartEvent(
                            reply_id=self.state.reply_id,
                            tool_call_id=tool_call["id"],
                            tool_call_name=tool_call["name"],
                        )
                        yield ToolResultTextDeltaEvent(
                            reply_id=self.state.reply_id,
                            tool_call_id=tool_call["id"],
                            delta=rejection_text,
                        )
                        yield ToolResultEndEvent(
                            reply_id=self.state.reply_id,
                            tool_call_id=tool_call["id"],
                            state="interrupted",
                        )
                        self._save_to_context(
                            [
                                ToolResultBlock(
                                    id=tool_call["id"],
                                    name=tool_call["name"],
                                    output=[TextBlock(text=rejection_text)],
                                    state="interrupted",
                                ),
                            ],
                        )

                processed_ids = {
                    r.get("tool_call", {}).get("id")
                    for r in event.confirm_results
                }
                if self.state.context:
                    last_content = self.state.context[-1].content
                    if isinstance(last_content, list):
                        for block in last_content:
                            if (
                                isinstance(block, ToolCallBlock)
                                and block.id in processed_ids
                            ):
                                block.await_user_confirmation = False

        else:
            self.state.cur_iter = 0
            self.state.reply_id = str(uuid.uuid4())
            self.state.confirmed_tool_call_ids = []

            yield RunStartedEvent(
                session_id="",
                reply_id=self.state.reply_id,
                name=self.name,
                role="assistant",
            )

        if isinstance(msgs, list):
            self.state.context.extend(msgs)
        elif msgs is not None:
            self.state.context.append(msgs)

        while self.state.cur_iter < self.max_iters:
            # Obtain the pending tool calls
            pending_tool_calls = self._get_pending_tool_calls()
            if len(pending_tool_calls) == 0:
                await self._compress_memory_if_needed()
                async for evt in self._reasoning(tool_choice="auto"):
                    yield evt

            # TODO: 这里首先需要分批次，然后一个批次一个批次进行处理
            batches = await self._get_batch_tool_calls()

            # Execute tool calls by batch
            for batch in batches:
                # First permission checking
                for tool_call in batch.tool_calls:
                    registered_tool = self.toolkit.tools.get(tool_call.name)
                    if registered_tool is None:
                        # Pass directly and leave the error handling in the
                        # tool execution to ensure the hook and middleware
                        # works for all tool calls
                        continue






            # Extract the tools to be executed and need user confirmation or
            # external execution
            awaiting_info = self._get_awaiting_tool_calls()
            awaiting_type = awaiting_info["awaiting_type"]
            awaiting_tool_calls = awaiting_info["awaiting_tool_calls"]
            pre_tool_calls = awaiting_info["pre_tool_calls"]

            for tool_call in pre_tool_calls:
                async for evt in self._acting(tool_call=tool_call):
                    yield evt
                self.state.confirmed_tool_call_ids = [
                    cid
                    for cid in self.state.confirmed_tool_call_ids
                    if cid != tool_call.id
                ]

            if awaiting_type is not None:
                if awaiting_type == EventType.REQUIRE_USER_CONFIRM:
                    yield RequireUserConfirmEvent(
                        reply_id=self.state.reply_id,
                        tool_calls=awaiting_tool_calls,
                    )
                else:
                    yield RequireExternalExecutionEvent(
                        reply_id=self.state.reply_id,
                        tool_calls=awaiting_tool_calls,
                    )
                waiting_text = (
                    "Waiting for user confirmation ..."
                    if awaiting_type == EventType.REQUIRE_USER_CONFIRM
                    else "Waiting for external execution ..."
                )
                yield AssistantMsg(
                    name=self.name,
                    content=[TextBlock(text=waiting_text)],
                )
                # Exit the loop and wait for the external event
                return

            if len(pre_tool_calls) == 0:
                break

            self.state.cur_iter += 1

        last_block = None
        if self.state.context:
            last_content = self.state.context[-1].content
            if isinstance(last_content, list) and last_content:
                last_block = last_content[-1]
        if last_block is None or not isinstance(last_block, TextBlock):
            async for evt in self._reasoning(tool_choice="none"):
                yield evt

        yield RunFinishedEvent(
            session_id="",
            reply_id=self.state.reply_id,
        )

        final_block = None
        if self.state.context:
            last_content = self.state.context[-1].content
            if isinstance(last_content, list) and last_content:
                final_block = last_content[-1]
        yield Msg(
            id=self.state.reply_id,
            name=self.name,
            content=[final_block] if final_block else [],
            role="assistant",
        )

    async def _get_batch_tool_calls(self) -> list[_ToolCallBatch]:
        """"""
        # All the tool calls waiting for execution
        tool_calls = self._get_pending_tool_calls()

        # Batch the tool calls according to whether they can be executed
        # concurrently or not
        batches: list[_ToolCallBatch] = []
        for tool_call in tool_calls:
            registered_tool = self.toolkit.tools.get(tool_call.name)

            # Treat unregistered or unavailable tools as concurrent tools since
            # it will not generate side effects and be blocked with acting
            if registered_tool is None or registered_tool.tool.is_concurrency_safe:
                if len(batches) == 0 or batches[-1].type != "concurrent":
                    batches.append(
                        _ToolCallBatch(
                            type="concurrent",
                            tool_calls=[tool_call],
                        )
                    )
                else:
                    batches[-1].tool_calls.append(tool_call)
            else:
                if len(batches) == 0 or batches[-1].type != "sequential":
                    batches.append(
                        _ToolCallBatch(
                            type="sequential",
                            tool_calls=[tool_call],
                        )
                    )
                else:
                    batches[-1].tool_calls.append(tool_call)

        return batches

    async def _reasoning(
        self,
        tool_choice: ToolChoice = "auto",
    ) -> AsyncGenerator:
        """Core reasoning logic. Yields chunks with is_last flag."""
        # TODO: Pass tool schemas from toolkit when toolkit is implemented

        yield ModelCallStartedEvent(
            reply_id=self.state.reply_id,
            model_name=self.model.model_name,
        )

        # The system prompt
        messages = [SystemMsg(name="system", content=await self._get_system_prompt())]
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

    async def _acting(
        self,
        tool_call: ToolCallBlock,
    ) -> AsyncGenerator:
        """Core acting logic. Yields chunks with is_last flag."""

        # First check if the tool is registered and available, than check
        # permissions.
        available = self.toolkit.is_available(
            tool_call.name, self.state.activated_groups
        )

        # Execute the permission checking if the tool is available
        if available:
            registered_tool = self.toolkit.tools.get(tool_call.name)

            # Check the tool permission by its builtin logic
            decision = await registered_tool.tool.check_permissions(
                _json_loads_with_repair(tool_call.input),
                self.state.permission_context,
            )

            # Check the permission

            if decision.behavior == PermissionBehavior.ASK:
                # TODO: update the tool call state in the context

                yield RequireUserConfirmEvent(
                    reply_id=self.state.reply_id,
                    tool_calls=[tool_call],
                )
                return

            # Both the deny and allow behavior will execute the tool
            yield ToolResultStartEvent(
                reply_id=self.state.reply_id,
                tool_call_id=tool_call.id,
                tool_call_name=tool_call.name,
            )

            if decision.behavior == PermissionBehavior.DENY:
                # Directly yield the result
                rejection_text = (
                    f"<system-info>**Note** the execution of tool "
                    f'"{tool_call.name}" is denied by permission checking!'
                    f'</system-info>'
                )
                chunk = ToolChunk(
                    content=[TextBlock(text=rejection_text)],
                    state="error"
                )
                yield self._convert_tool_chunk_to_event(
                    tool_call, chunk
                )
                # Save into context for record and yield ended event
                self._save_to_context(
                    [
                        ToolResultBlock(
                            id=tool_call.id,
                            name=tool_call.name,
                            output=[TextBlock(text=rejection_text)],
                            state="error",
                        ),
                    ]
                )
                yield ToolResultEndEvent(
                    reply_id=self.state.reply_id,
                    tool_call_id=tool_call.id,
                    state="error",
                )

                return

        else:
            # Not available will be handled in the tool execution with the
            # toolkit as execution result
            yield ToolResultStartEvent(
                reply_id=self.state.reply_id,
                tool_call_id=tool_call.id,
                tool_call_name=tool_call.name,
            )

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
                        )
                    ]
                )
                # The ended event for the tool result
                yield ToolResultEndEvent(
                    reply_id=self.state.reply_id,
                    tool_call_id=tool_call.id,
                    state=chunk.state,
                )
                return

            # Convert into events and yield
            async for evt in self._convert_tool_chunk_to_event(
                tool_call, chunk
            ):
                yield evt

    async def _observe(self, msgs: Msg | list[Msg] | None = None) -> None:
        """Receive external observation message(s) and save them into
        context."""
        await self.load_state()
        if isinstance(msgs, list):
            self.state.context.extend(msgs)
        elif msgs is not None:
            self.state.context.append(msgs)

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
            # If the current chunk has text blocks but no text block id, start with a start event
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
        finished_ids = set(block_ids["tools"]) - set([_.id for _ in tool_call_blocks])
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

    def _split_context_for_compression(self) -> tuple[list[Msg], list[Msg]]:
        """Split context into parts to compress and parts to keep recent."""
        # TODO: Implement full splitting logic when compression_config is
        #  available
        return [], list(self.state.context)

    async def _compress_memory_if_needed(self) -> None:
        """Compress the agent's memory if the token count exceeds the
        threshold."""
        # TODO: Implement when compression_config is available
