# -*- coding: utf-8 -*-
# TODO: simplify the ReActAgent class
# pylint: disable=not-an-iterable, too-many-lines
# mypy: disable-error-code="list-item"
"""ReAct agent class in agentscope."""
import asyncio
from enum import Enum
from typing import Type, Any, AsyncGenerator, Literal

from pydantic import BaseModel, ValidationError, Field

from ._utils import _AsyncNullContext
from ._react_agent_base import ReActAgentBase
from .._logging import logger
from ..formatter import FormatterBase
from ..memory import MemoryBase, LongTermMemoryBase, InMemoryMemory
from ..message import (
    Msg,
    ToolUseBlock,
    ToolResultBlock,
    TextBlock,
    AudioBlock,
)
from ..model import ChatModelBase
from ..rag import KnowledgeBase, Document
from ..plan import PlanNotebook
from ..token import TokenCounterBase
from ..tool import Toolkit, ToolResponse
from ..tracing import trace_reply
from ..tts import TTSModelBase


class _QueryRewriteModel(BaseModel):
    """The structured model used for query rewriting."""

    rewritten_query: str = Field(
        description=(
            "The rewritten query, which should be specific and concise. "
        ),
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


class _MemoryMark(str, Enum):
    """The memory marks used in the ReAct agent."""

    HINT = "hint"
    """Used to mark the hint messages that will be cleared after use."""

    COMPRESSED = "compressed"
    """Used to mark the compressed messages in the memory."""


class ReActAgent(ReActAgentBase):
    """A ReAct agent implementation in AgentScope, which supports

    - Realtime steering
    - API-based (parallel) tool calling
    - Hooks around reasoning, acting, reply, observe and print functions
    - Structured output generation
    """

    class CompressionConfig(BaseModel):
        """The compression related configuration in AgentScope"""

        model_config = {"arbitrary_types_allowed": True}
        """Allow arbitrary types in the pydantic model."""

        enable: bool
        """Whether to enable the auto compression feature."""

        agent_token_counter: TokenCounterBase
        """The token counter for the agent's model, which must be consistent
        with the model used in the agent."""

        trigger_threshold: int
        """The token threshold to trigger the compression process. When the
        total token count in the memory exceeds this threshold, the
        compression will be activated."""

        keep_recent: int = 3
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

        summary_schema: Type[BaseModel] = SummarySchema
        """The structured model used to guide the agent to generate the
        structured compressed summary."""

        compression_model: ChatModelBase | None = None
        """The compression model used to generate the compressed summary. If
        not provided, the agent's model will be used."""

        compression_formatter: FormatterBase | None = None
        """The corresponding formatter form the compression model, when the
        `compression_model` is provided, the `compression_formatter` must also
        be provided."""

    finish_function_name: str = "generate_response"
    """The name of the function used to generate structured output. Only
    registered when structured output model is provided in the reply call."""

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Toolkit | None = None,
        memory: MemoryBase | None = None,
        long_term_memory: LongTermMemoryBase | None = None,
        long_term_memory_mode: Literal[
            "agent_control",
            "static_control",
            "both",
        ] = "both",
        enable_meta_tool: bool = False,
        parallel_tool_calls: bool = False,
        knowledge: KnowledgeBase | list[KnowledgeBase] | None = None,
        enable_rewrite_query: bool = True,
        plan_notebook: PlanNotebook | None = None,
        print_hint_msg: bool = False,
        max_iters: int = 10,
        tts_model: TTSModelBase | None = None,
        compression_config: CompressionConfig | None = None,
    ) -> None:
        """Initialize the ReAct agent

        Args:
            name (`str`):
                The name of the agent.
            sys_prompt (`str`):
                The system prompt of the agent.
            model (`ChatModelBase`):
                The chat model used by the agent.
            formatter (`FormatterBase`):
                The formatter used to format the messages into the required
                format of the model API provider.
            toolkit (`Toolkit | None`, optional):
                A `Toolkit` object that contains the tool functions. If not
                provided, a default empty `Toolkit` will be created.
            memory (`MemoryBase | None`, optional):
                The memory used to store the dialogue history. If not provided,
                a default `InMemoryMemory` will be created, which stores
                messages in a list in memory.
            long_term_memory (`LongTermMemoryBase | None`, optional):
                The optional long-term memory, which will provide two tool
                functions: `retrieve_from_memory` and `record_to_memory`, and
                will attach the retrieved information to the system prompt
                before each reply.
            enable_meta_tool (`bool`, defaults to `False`):
                If `True`, a meta tool function `reset_equipped_tools` will be
                added to the toolkit, which allows the agent to manage its
                equipped tools dynamically.
            long_term_memory_mode (`Literal['agent_control', 'static_control',\
              'both']`, defaults to `both`):
                The mode of the long-term memory. If `agent_control`, two
                tool functions `retrieve_from_memory` and `record_to_memory`
                will be registered in the toolkit to allow the agent to
                manage the long-term memory. If `static_control`, retrieving
                and recording will happen in the beginning and end of
                each reply respectively.
            parallel_tool_calls (`bool`, defaults to `False`):
                When LLM generates multiple tool calls, whether to execute
                them in parallel.
            knowledge (`KnowledgeBase | list[KnowledgeBase] | None`, optional):
                The knowledge object(s) used by the agent to retrieve
                relevant documents at the beginning of each reply.
            enable_rewrite_query (`bool`, defaults to `True`):
                Whether ask the agent to rewrite the user input query before
                retrieving from the knowledge base(s), e.g. rewrite "Who am I"
                to "{user's name}" to get more relevant documents. Only works
                when the knowledge base(s) is provided.
            plan_notebook (`PlanNotebook | None`, optional):
                The plan notebook instance, allow the agent to finish the
                complex task by decomposing it into a sequence of subtasks.
            print_hint_msg (`bool`, defaults to `False`):
                Whether to print the hint messages, including the reasoning
                hint from the plan notebook, the retrieved information from
                the long-term memory and knowledge base(s).
            max_iters (`int`, defaults to `10`):
                The maximum number of iterations of the reasoning-acting loops.
            tts_model (`TTSModelBase | None` optional):
                The TTS model used by the agent.
            compression_config (`CompressionConfig | None`, optional):
                The compression configuration. If provided, the auto
                compression will be activated.
        """
        super().__init__()

        assert long_term_memory_mode in [
            "agent_control",
            "static_control",
            "both",
        ]

        # Static variables in the agent
        self.name = name
        self._sys_prompt = sys_prompt
        self.max_iters = max_iters
        self.model = model
        self.formatter = formatter
        self.tts_model = tts_model
        self.compression_config = compression_config

        # -------------- Memory management --------------
        # Record the dialogue history in the memory
        self.memory = memory or InMemoryMemory()
        # If provide the long-term memory, it will be used to retrieve info
        # in the beginning of each reply, and the result will be added to the
        # system prompt
        self.long_term_memory = long_term_memory

        # The long-term memory mode
        self._static_control = long_term_memory and long_term_memory_mode in [
            "static_control",
            "both",
        ]
        self._agent_control = long_term_memory and long_term_memory_mode in [
            "agent_control",
            "both",
        ]

        # -------------- Tool management --------------
        # If None, a default Toolkit will be created
        self.toolkit = toolkit or Toolkit()
        if self._agent_control:
            # Adding two tool functions into the toolkit to allow self-control
            self.toolkit.register_tool_function(
                long_term_memory.record_to_memory,
            )
            self.toolkit.register_tool_function(
                long_term_memory.retrieve_from_memory,
            )
        # Add a meta tool function to allow agent-controlled tool management
        if enable_meta_tool:
            self.toolkit.register_tool_function(
                self.toolkit.reset_equipped_tools,
            )

        self.parallel_tool_calls = parallel_tool_calls

        # -------------- RAG management --------------
        # The knowledge base(s) used by the agent
        if isinstance(knowledge, KnowledgeBase):
            knowledge = [knowledge]
        self.knowledge: list[KnowledgeBase] = knowledge or []
        self.enable_rewrite_query = enable_rewrite_query

        # -------------- Plan management --------------
        # Equipped the plan-related tools provided by the plan notebook as
        # a tool group named "plan_related". So that the agent can activate
        # the plan tools by the meta tool function
        self.plan_notebook = None
        if plan_notebook:
            self.plan_notebook = plan_notebook
            # When enable_meta_tool is True, plan tools are in plan_related
            # group and active by agent.
            # Otherwise, plan tools in basic group and always active.
            if enable_meta_tool:
                self.toolkit.create_tool_group(
                    "plan_related",
                    description=self.plan_notebook.description,
                )
                for tool in plan_notebook.list_tools():
                    self.toolkit.register_tool_function(
                        tool,
                        group_name="plan_related",
                    )
            else:
                for tool in plan_notebook.list_tools():
                    self.toolkit.register_tool_function(
                        tool,
                    )

        # If print the reasoning hint messages
        self.print_hint_msg = print_hint_msg

        # The maximum number of iterations of the reasoning-acting loops
        self.max_iters = max_iters

        # Variables to record the intermediate state

        # If required structured output model is provided
        self._required_structured_model: Type[BaseModel] | None = None

        # -------------- State registration and hooks --------------
        # Register the status variables
        self.register_state("name")
        self.register_state("_sys_prompt")

    @property
    def sys_prompt(self) -> str:
        """The dynamic system prompt of the agent."""
        agent_skill_prompt = self.toolkit.get_agent_skill_prompt()
        if agent_skill_prompt:
            return self._sys_prompt + "\n\n" + agent_skill_prompt
        else:
            return self._sys_prompt

    @trace_reply
    async def reply(  # pylint: disable=too-many-branches
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """Generate a reply based on the current state and input arguments.

        Args:
            msg (`Msg | list[Msg] | None`, optional):
                The input message(s) to the agent.
            structured_model (`Type[BaseModel] | None`, optional):
                The required structured output model. If provided, the agent
                is expected to generate structured output in the `metadata`
                field of the output message.

        Returns:
            `Msg`:
                The output message generated by the agent.
        """
        # Record the input message(s) in the memory
        await self.memory.add(msg)

        # -------------- Retrieval process --------------
        # Retrieve relevant records from the long-term memory if activated
        await self._retrieve_from_long_term_memory(msg)
        # Retrieve relevant documents from the knowledge base(s) if any
        await self._retrieve_from_knowledge(msg)

        # Control if LLM generates tool calls in each reasoning step
        tool_choice: Literal["auto", "none", "required"] | None = None

        # -------------- Structured output management --------------
        self._required_structured_model = structured_model
        # Record structured output model if provided
        if structured_model:
            # Register generate_response tool only when structured output
            # is required
            if self.finish_function_name not in self.toolkit.tools:
                self.toolkit.register_tool_function(
                    getattr(self, self.finish_function_name),
                )

            # Set the structured output model
            self.toolkit.set_extended_model(
                self.finish_function_name,
                structured_model,
            )
            tool_choice = "required"
        else:
            # Remove generate_response tool if no structured output is required
            self.toolkit.remove_tool_function(self.finish_function_name)

        # -------------- The reasoning-acting loop --------------
        # Cache the structured output generated in the finish function call
        structured_output = None
        reply_msg = None
        for _ in range(self.max_iters):
            # -------------- Memory compression --------------
            await self._compress_memory_if_needed()

            # -------------- The reasoning process --------------
            msg_reasoning = await self._reasoning(tool_choice)

            # -------------- The acting process --------------
            futures = [
                self._acting(tool_call)
                for tool_call in msg_reasoning.get_content_blocks(
                    "tool_use",
                )
            ]
            # Parallel tool calls or not
            if self.parallel_tool_calls:
                structured_outputs = await asyncio.gather(*futures)
            else:
                # Sequential tool calls
                structured_outputs = [await _ for _ in futures]

            # -------------- Check for exit condition --------------
            # If structured output is still not satisfied
            if self._required_structured_model:
                # Remove None results
                structured_outputs = [_ for _ in structured_outputs if _]

                msg_hint = None
                # If the acting step generates structured outputs
                if structured_outputs:
                    # Cache the structured output data
                    structured_output = structured_outputs[-1]

                    # Prepare textual response
                    if msg_reasoning.has_content_blocks("text"):
                        # Re-use the existing text response if any to avoid
                        # duplicate text generation
                        reply_msg = Msg(
                            self.name,
                            msg_reasoning.get_content_blocks("text"),
                            "assistant",
                            metadata=structured_output,
                        )
                        break

                    # Generate a textual response in the next iteration
                    msg_hint = Msg(
                        "user",
                        "<system-hint>Now generate a text "
                        "response based on your current situation"
                        "</system-hint>",
                        "user",
                    )
                    await self.memory.add(
                        msg_hint,
                        marks=_MemoryMark.HINT,
                    )

                    # Just generate text response in the next reasoning step
                    tool_choice = "none"
                    # The structured output is generated successfully
                    self._required_structured_model = None

                elif not msg_reasoning.has_content_blocks("tool_use"):
                    # If structured output is required but no tool call is
                    # made, remind the llm to go on the task
                    msg_hint = Msg(
                        "user",
                        "<system-hint>Structured output is "
                        f"required, go on to finish your task or call "
                        f"'{self.finish_function_name}' to generate the "
                        f"required structured output.</system-hint>",
                        "user",
                    )
                    await self.memory.add(msg_hint, marks=_MemoryMark.HINT)
                    # Require tool call in the next reasoning step
                    tool_choice = "required"

                if msg_hint and self.print_hint_msg:
                    await self.print(msg_hint)

            elif not msg_reasoning.has_content_blocks("tool_use"):
                # Exit the loop when no structured output is required (or
                # already satisfied) and only text response is generated
                msg_reasoning.metadata = structured_output
                reply_msg = msg_reasoning
                break

        # When the maximum iterations are reached
        # and no reply message is generated
        if reply_msg is None:
            reply_msg = await self._summarizing()
            reply_msg.metadata = structured_output
            await self.memory.add(reply_msg)

        # Post-process the memory, long-term memory
        if self._static_control:
            await self.long_term_memory.record(
                [
                    *await self.memory.get_memory(
                        exclude_mark=_MemoryMark.COMPRESSED,
                    ),
                ],
            )

        return reply_msg

    # pylint: disable=too-many-branches
    async def _reasoning(
        self,
        tool_choice: Literal["auto", "none", "required"] | None = None,
    ) -> Msg:
        """Perform the reasoning process."""

        if self.plan_notebook:
            # Insert the reasoning hint from the plan notebook
            hint_msg = await self.plan_notebook.get_current_hint()
            if self.print_hint_msg and hint_msg:
                await self.print(hint_msg)
            await self.memory.add(hint_msg, marks=_MemoryMark.HINT)

        # Convert Msg objects into the required format of the model API
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", self.sys_prompt, "system"),
                *await self.memory.get_memory(),
            ],
        )
        # Clear the hint messages after use
        await self.memory.delete_by_mark(mark=_MemoryMark.HINT)

        res = await self.model(
            prompt,
            tools=self.toolkit.get_json_schemas(),
            tool_choice=tool_choice,
        )

        # handle output from the model
        interrupted_by_user = False
        msg = None

        # TTS model context manager
        tts_context = self.tts_model or _AsyncNullContext()
        speech: AudioBlock | list[AudioBlock] | None = None

        try:
            async with tts_context:
                msg = Msg(name=self.name, content=[], role="assistant")
                if self.model.stream:
                    async for content_chunk in res:
                        msg.content = content_chunk.content

                        # The speech generated from multimodal (audio) models
                        # e.g. Qwen-Omni and GPT-AUDIO
                        speech = msg.get_content_blocks("audio") or None

                        # Push to TTS model if available
                        if (
                            self.tts_model
                            and self.tts_model.supports_streaming_input
                        ):
                            tts_res = await self.tts_model.push(msg)
                            speech = tts_res.content

                        await self.print(msg, False, speech=speech)

                else:
                    msg.content = list(res.content)

                if self.tts_model:
                    # Push to TTS model and block to receive the full speech
                    # synthesis result
                    tts_res = await self.tts_model.synthesize(msg)
                    if self.tts_model.stream:
                        async for tts_chunk in tts_res:
                            speech = tts_chunk.content
                            await self.print(msg, False, speech=speech)
                    else:
                        speech = tts_res.content

                await self.print(msg, True, speech=speech)

                # Add a tiny sleep to yield the last message object in the
                # message queue
                await asyncio.sleep(0.001)

        except asyncio.CancelledError as e:
            interrupted_by_user = True
            raise e from None

        finally:
            # None will be ignored by the memory
            await self.memory.add(msg)

            # Post-process for user interruption
            if interrupted_by_user and msg:
                # Fake tool results
                tool_use_blocks: list = msg.get_content_blocks(
                    "tool_use",
                )
                for tool_call in tool_use_blocks:
                    msg_res = Msg(
                        "system",
                        [
                            ToolResultBlock(
                                type="tool_result",
                                id=tool_call["id"],
                                name=tool_call["name"],
                                output="The tool call has been interrupted "
                                "by the user.",
                            ),
                        ],
                        "system",
                    )
                    await self.memory.add(msg_res)
                    await self.print(msg_res, True)
        return msg

    async def _acting(self, tool_call: ToolUseBlock) -> dict | None:
        """Perform the acting process, and return the structured output if
        it's generated and verified in the finish function call.

        Args:
            tool_call (`ToolUseBlock`):
                The tool use block to be executed.

        Returns:
            `Union[dict, None]`:
                Return the structured output if it's verified in the finish
                function call, otherwise return None.
        """

        tool_res_msg = Msg(
            "system",
            [
                ToolResultBlock(
                    type="tool_result",
                    id=tool_call["id"],
                    name=tool_call["name"],
                    output=[],
                ),
            ],
            "system",
        )
        try:
            # Execute the tool call
            tool_res = await self.toolkit.call_tool_function(tool_call)

            # Async generator handling
            async for chunk in tool_res:
                # Turn into a tool result block
                tool_res_msg.content[0][  # type: ignore[index]
                    "output"
                ] = chunk.content

                await self.print(tool_res_msg, chunk.is_last)

                # Raise the CancelledError to handle the interruption in the
                # handle_interrupt function
                if chunk.is_interrupted:
                    raise asyncio.CancelledError()

                # Return message if generate_response is called successfully
                if (
                    tool_call["name"] == self.finish_function_name
                    and chunk.metadata
                    and chunk.metadata.get("success", False)
                ):
                    # Only return the structured output
                    return chunk.metadata.get("structured_output")

            return None

        finally:
            # Record the tool result message in the memory
            await self.memory.add(tool_res_msg)

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Receive observing message(s) without generating a reply.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message or messages to be observed.
        """
        await self.memory.add(msg)

    async def _summarizing(self) -> Msg:
        """Generate a response when the agent fails to solve the problem in
        the maximum iterations."""

        hint_msg = Msg(
            "user",
            "You have failed to generate response within the maximum "
            "iterations. Now respond directly by summarizing the current "
            "situation.",
            role="user",
        )

        # Generate a reply by summarizing the current situation
        prompt = await self.formatter.format(
            [
                Msg("system", self.sys_prompt, "system"),
                *await self.memory.get_memory(),
                hint_msg,
            ],
        )
        # TODO: handle the structured output here, maybe force calling the
        #  finish_function here
        res = await self.model(prompt)

        # TTS model context manager
        tts_context = self.tts_model or _AsyncNullContext()
        speech: AudioBlock | list[AudioBlock] | None = None

        async with tts_context:
            res_msg = Msg(self.name, [], "assistant")
            if isinstance(res, AsyncGenerator):
                async for chunk in res:
                    res_msg.content = chunk.content

                    # The speech generated from multimodal (audio) models
                    # e.g. Qwen-Omni and GPT-AUDIO
                    speech = res_msg.get_content_blocks("audio") or None

                    # Push to TTS model if available
                    if (
                        self.tts_model
                        and self.tts_model.supports_streaming_input
                    ):
                        tts_res = await self.tts_model.push(res_msg)
                        speech = tts_res.content

                    await self.print(res_msg, False, speech=speech)

            else:
                res_msg.content = res.content

            if self.tts_model:
                # Push to TTS model and block to receive the full speech
                # synthesis result
                tts_res = await self.tts_model.synthesize(res_msg)
                if self.tts_model.stream:
                    async for tts_chunk in tts_res:
                        speech = tts_chunk.content
                        await self.print(res_msg, False, speech=speech)
                else:
                    speech = tts_res.content

            await self.print(res_msg, True, speech=speech)

            return res_msg

    # pylint: disable=unused-argument
    async def handle_interrupt(
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """The post-processing logic when the reply is interrupted by the
        user or something else.

        Args:
            msg (`Msg | list[Msg] | None`, optional):
                The input message(s) to the agent.
            structured_model (`Type[BaseModel] | None`, optional):
                The required structured output model.
        """

        response_msg = Msg(
            self.name,
            "I noticed that you have interrupted me. What can I "
            "do for you?",
            "assistant",
            metadata={
                # Expose this field to indicate the interruption
                "_is_interrupted": True,
            },
        )

        await self.print(response_msg, True)
        await self.memory.add(response_msg)
        return response_msg

    def generate_response(
        self,
        **kwargs: Any,
    ) -> ToolResponse:
        """
        Generate required structured output by this function and return it
        """

        structured_output = None
        # Prepare structured output
        if self._required_structured_model:
            try:
                # Use the metadata field of the message to store the
                # structured output
                structured_output = (
                    self._required_structured_model.model_validate(
                        kwargs,
                    ).model_dump()
                )

            except ValidationError as e:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=f"Arguments Validation Error: {e}",
                        ),
                    ],
                    metadata={
                        "success": False,
                        "structured_output": {},
                    },
                )
        else:
            logger.warning(
                "The generate_response function is called when no structured "
                "output model is required.",
            )

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Successfully generated response.",
                ),
            ],
            metadata={
                "success": True,
                "structured_output": structured_output,
            },
            is_last=True,
        )

    async def _retrieve_from_long_term_memory(
        self,
        msg: Msg | list[Msg] | None,
    ) -> None:
        """Insert the retrieved information from the long-term memory into
        the short-term memory as a Msg object.

        Args:
            msg (`Msg | list[Msg] | None`):
                The input message to the agent.
        """
        if self._static_control and msg:
            # Retrieve information from the long-term memory if available
            retrieved_info = await self.long_term_memory.retrieve(msg)
            if retrieved_info:
                retrieved_msg = Msg(
                    name="long_term_memory",
                    content="<long_term_memory>The content below are "
                    "retrieved from long-term memory, which maybe "
                    f"useful:\n{retrieved_info}</long_term_memory>",
                    role="user",
                )
                if self.print_hint_msg:
                    await self.print(retrieved_msg, True)
                await self.memory.add(retrieved_msg)

    async def _retrieve_from_knowledge(
        self,
        msg: Msg | list[Msg] | None,
    ) -> None:
        """Insert the retrieved documents from the RAG knowledge base(s) if
        available.

        Args:
            msg (`Msg | list[Msg] | None`):
                The input message to the agent.
        """
        if self.knowledge and msg:
            # Prepare the user input query
            query = None
            if isinstance(msg, Msg):
                query = msg.get_text_content()
            elif isinstance(msg, list):
                texts = []
                for m in msg:
                    text = m.get_text_content()
                    if text:
                        texts.append(text)
                query = "\n".join(texts)

            # Skip if the query is empty
            if not query:
                return

            # Rewrite the query by the LLM if enabled
            if self.enable_rewrite_query:
                stream_tmp = self.model.stream
                try:
                    rewrite_prompt = await self.formatter.format(
                        msgs=[
                            Msg("system", self.sys_prompt, "system"),
                            *await self.memory.get_memory(),
                            Msg(
                                "user",
                                "<system-hint>Now you need to rewrite "
                                "the above user query to be more specific and "
                                "concise for knowledge retrieval. For "
                                "example, rewrite the query 'what happened "
                                "last day' to 'what happened on 2023-10-01' "
                                "(assuming today is 2023-10-02)."
                                "</system-hint>",
                                "user",
                            ),
                        ],
                    )
                    self.model.stream = False
                    res = await self.model(
                        rewrite_prompt,
                        structured_model=_QueryRewriteModel,
                    )
                    if res.metadata and res.metadata.get("rewritten_query"):
                        query = res.metadata["rewritten_query"]

                except Exception as e:
                    logger.warning(
                        "Skipping the query rewriting due to error: %s",
                        str(e),
                    )
                finally:
                    self.model.stream = stream_tmp

            docs: list[Document] = []
            for kb in self.knowledge:
                # retrieve the user input query
                docs.extend(
                    await kb.retrieve(query=query),
                )
            if docs:
                # Rerank by the relevance score
                docs = sorted(
                    docs,
                    key=lambda doc: doc.score or 0.0,
                    reverse=True,
                )
                # Prepare the retrieved knowledge string
                retrieved_msg = Msg(
                    name="user",
                    content=[
                        TextBlock(
                            type="text",
                            text=(
                                "<retrieved_knowledge>Use the following "
                                "content from the knowledge base(s) if it's "
                                "helpful:\n"
                            ),
                        ),
                        *[_.metadata.content for _ in docs],
                        TextBlock(
                            type="text",
                            text="</retrieved_knowledge>",
                        ),
                    ],
                    role="user",
                )
                if self.print_hint_msg:
                    await self.print(retrieved_msg, True)
                await self.memory.add(retrieved_msg)

    async def _compress_memory_if_needed(self) -> None:
        """Compress the memory content if needed."""
        if (
            self.compression_config is None
            or not self.compression_config.enable
        ):
            return

        # Obtain the messages that have not been compressed yet
        to_compressed_msgs = await self.memory.get_memory(
            exclude_mark=_MemoryMark.COMPRESSED,
        )

        # keep the recent n messages uncompressed, note messages with tool
        #  use and result pairs should be kept together
        n_keep = 0
        accumulated_tool_call_ids = set()
        for i in range(len(to_compressed_msgs) - 1, -1, -1):
            msg = to_compressed_msgs[i]
            for block in msg.get_content_blocks("tool_result"):
                accumulated_tool_call_ids.add(block["id"])

            for block in msg.get_content_blocks("tool_use"):
                if block["id"] in accumulated_tool_call_ids:
                    accumulated_tool_call_ids.remove(block["id"])

            # Handle the tool use/result pairs
            if len(accumulated_tool_call_ids) == 0:
                n_keep += 1

            # Break if reach the number of messages to keep
            if n_keep >= self.compression_config.keep_recent:
                # Remove the messages that should be kept uncompressed
                to_compressed_msgs = to_compressed_msgs[:i]
                break

        # Skip compression if no messages to compress
        if not to_compressed_msgs:
            return

        # Calculate the token
        prompt = await self.formatter.format(
            [
                Msg("system", self.sys_prompt, "system"),
                *to_compressed_msgs,
            ],
        )
        n_tokens = await self.compression_config.agent_token_counter.count(
            prompt,
        )

        if n_tokens > self.compression_config.trigger_threshold:
            logger.info(
                "Memory compression is triggered (%d > "
                "threshold %d) for agent %s.",
                n_tokens,
                self.compression_config.trigger_threshold,
                self.name,
            )

            # The formatter used for compression
            compression_formatter = (
                self.compression_config.compression_formatter or self.formatter
            )

            # Prepare the prompt used to compress the memories
            compression_prompt = await compression_formatter.format(
                [
                    Msg("system", self.sys_prompt, "system"),
                    *to_compressed_msgs,
                    Msg(
                        "user",
                        self.compression_config.compression_prompt,
                        "user",
                    ),
                ],
            )

            # TODO: What if the compressed messages include multimodal blocks?
            # Use the specified compression model if provided
            compression_model = (
                self.compression_config.compression_model or self.model
            )
            res = await compression_model(
                compression_prompt,
                structured_model=(self.compression_config.summary_schema),
            )

            # Obtain the structured output from the model response
            last_chunk = None
            if compression_model.stream:
                async for chunk in res:
                    last_chunk = chunk
            else:
                last_chunk = res

            # Format the compressed memory summary
            if last_chunk.metadata:
                # Update the compressed summary in the memory storage
                await self.memory.update_compressed_summary(
                    self.compression_config.summary_template.format(
                        **last_chunk.metadata,
                    ),
                )

                # Mark the compressed messages in the memory storage
                await self.memory.update_messages_mark(
                    msg_ids=[_.id for _ in to_compressed_msgs],
                    new_mark=_MemoryMark.COMPRESSED,
                )

                logger.info(
                    "Finished compressing %d messages in agent %s.",
                    len(to_compressed_msgs),
                    self.name,
                )

            else:
                logger.warning(
                    "Failed to obtain compression summary from the model "
                    "structured output in agent %s.",
                    self.name,
                )
