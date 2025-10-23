# -*- coding: utf-8 -*-
# pylint: disable=not-an-iterable
# mypy: disable-error-code="list-item"
"""ReAct agent class in agentscope."""
import asyncio
from typing import Type, Any, AsyncGenerator, Literal

import shortuuid
from pydantic import BaseModel, ValidationError, Field

from ._react_agent_base import ReActAgentBase
from .._logging import logger
from ..formatter import FormatterBase
from ..memory import MemoryBase, LongTermMemoryBase, InMemoryMemory
from ..message import Msg, ToolUseBlock, ToolResultBlock, TextBlock
from ..model import ChatModelBase
from ..rag import KnowledgeBase, Document
from ..plan import PlanNotebook
from ..tool import Toolkit, ToolResponse
from ..tracing import trace_reply


class _QueryRewriteModel(BaseModel):
    """The structured model used for query rewriting."""

    rewritten_query: str = Field(
        description=(
            "The rewritten query, which should be specific and concise. "
        ),
    )


def finish_function_pre_print_hook(
    self: "ReActAgent",
    kwargs: dict[str, Any],
) -> dict[str, Any] | None:
    """A pre-speak hook function that check if finish_function is called. If
    so, it will wrap the response argument into a message and return it to
    replace the original message. By this way, the calling of the finish
    function will be displayed as a text reply instead of a tool call."""

    msg = kwargs["msg"]

    if isinstance(msg.content, str):
        return None

    if isinstance(msg.content, list):
        for i, block in enumerate(msg.content):
            if (
                block["type"] == "tool_use"
                and block["name"] == self.finish_function_name
            ):
                # Convert the response argument into a text block for
                # displaying
                try:
                    msg.content[i] = TextBlock(
                        type="text",
                        text=block["input"].get("response", ""),
                    )
                    return kwargs
                except Exception:
                    print("Error in block input", block["input"])

    return None


class ReActAgent(ReActAgentBase):
    """A ReAct agent implementation in AgentScope, which supports

    - Realtime steering
    - API-based (parallel) tool calling
    - Hooks around reasoning, acting, reply, observe and print functions
    - Structured output generation
    """

    finish_function_name: str = "generate_response"
    """The function name used to finish replying and return a response to
    the user."""

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
        self.toolkit.register_tool_function(
            getattr(self, self.finish_function_name),
        )
        if self._agent_control:
            # Adding two tool functions into the toolkit to allow self-control
            self.toolkit.register_tool_function(
                long_term_memory.record_to_memory,
            )
            self.toolkit.register_tool_function(
                long_term_memory.retrieve_from_memory,
            )
        # Add a meta tool function to allow agent-controlled tool management
        if enable_meta_tool or plan_notebook:
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
            self.toolkit.create_tool_group(
                "plan_related",
                description=self.plan_notebook.description,
            )
            for tool in plan_notebook.list_tools():
                self.toolkit.register_tool_function(
                    tool,
                    group_name="plan_related",
                )

        # If print the reasoning hint messages
        self.print_hint_msg = print_hint_msg

        # The maximum number of iterations of the reasoning-acting loops
        self.max_iters = max_iters

        # The hint messages that will be attached to the prompt to guide the
        # agent's behavior before each reasoning step, and cleared after
        # each reasoning step, meaning the hint messages is one-time use only.
        # We use an InMemoryMemory instance to store the hint messages
        self._reasoning_hint_msgs = InMemoryMemory()

        # Variables to record the intermediate state

        # If required structured output model is provided
        self._required_structured_model: Type[BaseModel] | None = None

        # -------------- State registration and hooks --------------
        # Register the status variables
        self.register_state("name")
        self.register_state("_sys_prompt")

        self.register_instance_hook(
            "pre_print",
            "finish_function_pre_print_hook",
            finish_function_pre_print_hook,
        )

    @property
    def sys_prompt(self) -> str:
        """The dynamic system prompt of the agent."""
        return self._sys_prompt

    @trace_reply
    async def reply(
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

        # Retrieve relevant records from the long-term memory if activated
        await self._retrieve_from_long_term_memory(msg)
        # Retrieve relevant documents from the knowledge base(s) if any
        await self._retrieve_from_knowledge(msg)

        self._required_structured_model = structured_model
        # Record structured output model if provided
        if structured_model:
            self.toolkit.set_extended_model(
                self.finish_function_name,
                structured_model,
            )

        # The reasoning-acting loop
        reply_msg = None
        for _ in range(self.max_iters):
            msg_reasoning = await self._reasoning()

            futures = [
                self._acting(tool_call)
                for tool_call in msg_reasoning.get_content_blocks(
                    "tool_use",
                )
            ]

            # Parallel tool calls or not
            if self.parallel_tool_calls:
                acting_responses = await asyncio.gather(*futures)

            else:
                # Sequential tool calls
                acting_responses = [await _ for _ in futures]

            # Find the first non-None replying message from the acting
            for acting_msg in acting_responses:
                reply_msg = reply_msg or acting_msg

            if reply_msg:
                break

        # When the maximum iterations are reached
        if reply_msg is None:
            reply_msg = await self._summarizing()

        # Post-process the memory, long-term memory
        if self._static_control:
            await self.long_term_memory.record(
                [
                    *([*msg] if isinstance(msg, list) else [msg]),
                    *await self.memory.get_memory(),
                    reply_msg,
                ],
            )

        await self.memory.add(reply_msg)
        return reply_msg

    async def _reasoning(
        self,
    ) -> Msg:
        """Perform the reasoning process."""
        if self.plan_notebook:
            # Insert the reasoning hint from the plan notebook
            hint_msg = await self.plan_notebook.get_current_hint()
            if self.print_hint_msg and hint_msg:
                await self.print(hint_msg)
            await self._reasoning_hint_msgs.add(hint_msg)

        # Convert Msg objects into the required format of the model API
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", self.sys_prompt, "system"),
                *await self.memory.get_memory(),
                # The hint messages to guide the agent's behavior, maybe empty
                *await self._reasoning_hint_msgs.get_memory(),
            ],
        )
        # Clear the hint messages after use
        await self._reasoning_hint_msgs.clear()

        res = await self.model(
            prompt,
            tools=self.toolkit.get_json_schemas(),
        )

        # handle output from the model
        interrupted_by_user = False
        msg = None
        try:
            if self.model.stream:
                msg = Msg(self.name, [], "assistant")
                async for content_chunk in res:
                    msg.content = content_chunk.content
                    await self.print(msg, False)
                await self.print(msg, True)

            else:
                msg = Msg(self.name, list(res.content), "assistant")
                await self.print(msg, True)

        except asyncio.CancelledError as e:
            interrupted_by_user = True
            raise e from None

        finally:
            if msg and not msg.has_content_blocks("tool_use"):
                # Turn plain text response into a tool call of the finish
                # function
                msg = Msg.from_dict(msg.to_dict())
                msg.content = [
                    ToolUseBlock(
                        id=shortuuid.uuid(),
                        type="tool_use",
                        name=self.finish_function_name,
                        input={"response": msg.get_text_content()},
                    ),
                ]

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

    async def _acting(self, tool_call: ToolUseBlock) -> Msg | None:
        """Perform the acting process.

        Args:
            tool_call (`ToolUseBlock`):
                The tool use block to be executed.

        Returns:
            `Union[Msg, None]`:
                Return a message to the user if the `finish_function` is
                called, otherwise return `None`.
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

            response_msg = None
            # Async generator handling
            async for chunk in tool_res:
                # Turn into a tool result block
                tool_res_msg.content[0][  # type: ignore[index]
                    "output"
                ] = chunk.content

                # Skip the printing of the finish function call
                if (
                    tool_call["name"] != self.finish_function_name
                    or tool_call["name"] == self.finish_function_name
                    and (
                        chunk.metadata is None
                        or not chunk.metadata.get("success")
                    )
                ):
                    await self.print(tool_res_msg, chunk.is_last)

                # Return message if generate_response is called successfully
                if (
                    tool_call["name"] == self.finish_function_name
                    and chunk.metadata
                    and chunk.metadata.get(
                        "success",
                        True,
                    )
                ):
                    response_msg = chunk.metadata.get("response_msg")

            return response_msg

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

        res_msg = Msg(self.name, [], "assistant")
        if isinstance(res, AsyncGenerator):
            async for chunk in res:
                res_msg.content = chunk.content
                await self.print(res_msg, False)
            await self.print(res_msg, True)

        else:
            res_msg.content = res.content
            await self.print(res_msg, True)

        return res_msg

    async def handle_interrupt(
        self,
        _msg: Msg | list[Msg] | None = None,
    ) -> Msg:
        """The post-processing logic when the reply is interrupted by the
        user or something else."""

        response_msg = Msg(
            self.name,
            "I noticed that you have interrupted me. What can I "
            "do for you?",
            "assistant",
            metadata={},
        )

        await self.print(response_msg, True)
        await self.memory.add(response_msg)
        return response_msg

    def generate_response(
        self,
        response: str,
        **kwargs: Any,
    ) -> ToolResponse:
        """Generate a response. Note only the input argument `response` is
        visible to the others, you should include all the necessary
        information in the `response` argument.

        Args:
            response (`str`):
                Your response to the user.
        """
        response_msg = Msg(
            self.name,
            response,
            "assistant",
        )

        # Prepare structured output
        if self._required_structured_model:
            try:
                # Use the metadata field of the message to store the
                # structured output
                response_msg.metadata = (
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
                        "response_msg": None,
                    },
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
                "response_msg": response_msg,
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
                query = "\n".join(_.get_text_content() for _ in msg)

            # Skip if the query is empty
            if not query:
                return

            # Rewrite the query by the LLM if enabled
            if self.enable_rewrite_query:
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
                    stream_tmp = self.model.stream
                    self.model.stream = False
                    res = await self.model(
                        rewrite_prompt,
                        structured_model=_QueryRewriteModel,
                    )
                    self.model.stream = stream_tmp
                    if res.metadata and res.metadata.get("rewritten_query"):
                        query = res.metadata["rewritten_query"]

                except Exception as e:
                    logger.warning(
                        "Skipping the query rewriting due to error: %s",
                        str(e),
                    )

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
