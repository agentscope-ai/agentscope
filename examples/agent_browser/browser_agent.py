# -*- coding: utf-8 -*-
"""Browser Agent"""
# pylint: disable=W0212
# pylint: disable=too-many-lines
import re
import uuid
import os
import json
from typing import Type, Optional, Any
import asyncio
import base64
from pydantic import BaseModel, ValidationError
from browser_search import BrowserSearchMixin
from agentscope.agent import ReActAgent
from agentscope.formatter import FormatterBase
from agentscope.memory import MemoryBase
from agentscope.message import (
    Msg,
    ToolUseBlock,
    TextBlock,
    ToolResultBlock,
)
from agentscope.model import ChatModelBase
from agentscope.tool import (
    Toolkit,
    ToolResponse,
)
from agentscope.token import TokenCounterBase, OpenAITokenCounter

with open(
    "examples/agent_browser/build_in_prompt/browser_agent_sys_prompt.md",
    "r",
    encoding="utf-8",
) as f:
    _BROWSER_AGENT_DEFAULT_SYS_PROMPT = f.read()
with open(
    "examples/agent_browser/build_in_prompt/browser_agent_reasoning_prompt.md",
    "r",
    encoding="utf-8",
) as f:
    _BROWSER_AGENT_DEFAULT_REASONING_PROMPT = f.read()
with open(
    "examples/agent_browser/build_in_prompt/browser_agent_task_decomposition_prompt.md",  # noqa: E501 pylint: disable=C0301
    "r",
    encoding="utf-8",
) as f:
    _BROWSER_AGENT_DEFAULT_TASK_DECOMPOSITION_PROMPT = f.read()
with open(
    "examples/agent_browser/build_in_prompt/browser_agent_summarize_task.md",
    "r",
    encoding="utf-8",
) as f:
    _BROWSER_AGENT_SUMMARIZE_TASK_PROMPT = f.read()


class BrowserAgent(ReActAgent, BrowserSearchMixin):
    """
    Browser Agent that extends ReActAgent with browser-specific capabilities.

    The agent leverages MCP (Model Context Protocol) servers to access browser
    tools with Playwright, enabling sophisticated web automation tasks.

    Example:
        .. code-block:: python

            agent = BrowserAgent(
                name="web_navigator",
                model=my_chat_model,
                formatter=my_formatter,
                memory=my_memory,
                toolkit=browser_toolkit,
                start_url="https://example.com"
            )

            response = await agent.reply("Search for Python tutorials")
    """

    def __init__(
        self,
        name: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        memory: MemoryBase,
        toolkit: Toolkit,
        sys_prompt: str = _BROWSER_AGENT_DEFAULT_SYS_PROMPT,
        max_iters: int = 50,
        start_url: Optional[str] = "https://www.google.com",
        reasoning_prompt: str = _BROWSER_AGENT_DEFAULT_REASONING_PROMPT,
        task_decomposition_prompt: str = (
            _BROWSER_AGENT_DEFAULT_TASK_DECOMPOSITION_PROMPT
        ),
        token_counter: TokenCounterBase = OpenAITokenCounter("gpt-4o"),
        max_mem_length: int = 20,
        use_dfs_reply: bool = False,
    ) -> None:
        """Initialize the Browser Agent.

        Args:
            name (str):
                The unique identifier name for the agent instance.
            model (ChatModelBase):
                The chat model used for generating responses and reasoning.
            formatter (FormatterBase):
                The formatter used to convert messages into the required format
                 for the model API.
            memory (MemoryBase):
                The memory component used to store and retrieve dialogue
                history.
            toolkit (Toolkit):
                A toolkit object containing the browser tool functions and
                utilities.
            sys_prompt (str, optional):
                The system prompt that defines the agent's behavior and
                personality.
                Defaults to _BROWSER_AGENT_DEFAULT_SYS_PROMPT.
            max_iters (int, optional):
                The maximum number of reasoning-acting loop iterations.
                Defaults to 50.
            start_url (Optional[str], optional):
                The initial URL to navigate to when the agent starts.
                Defaults to "https://www.google.com".
            reasoning_prompt (str, optional):
                The prompt used during the reasoning phase to guide
                decision-making.
                Defaults to _BROWSER_AGENT_REASONING_PROMPT.
            use_dfs_reply (bool, optional):
                Whether to use the DFS replay method instead of the default
                reply method. Defaults to False.

        Returns:
            None
        """

        self.start_url = start_url
        self._has_initial_navigated = False
        self.reasoning_prompt = reasoning_prompt
        self.task_decomposition_prompt = task_decomposition_prompt
        self.max_memory_length = max_mem_length
        self.token_estimator = token_counter
        self.snapshot_chunk_id = 0
        self.chunk_continue_status = False
        self.previous_chunkwise_information = ""
        self.snapshot_in_chunk = []
        self.subtasks = []
        self.original_task = ""
        self.current_subtask_idx = 0
        self.current_subtask = None
        self.iter_n = 0
        self.finish_function_name = "browser_generate_final_response"
        self.init_query = ""
        self._required_structured_model: Type[BaseModel] | None = None
        self.use_dfs_reply = use_dfs_reply

        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            memory=memory,
            toolkit=toolkit,
            max_iters=max_iters,
        )

        self.toolkit.register_tool_function(self.browser_subtask_manager)

        if (
            self.model.model_name.startswith("qvq")
            or "-vl" in self.model.model_name
        ):
            # If the model supports multimodal input,
            # prepare a directory for screenshots
            screenshot_dir = os.path.join(
                "./logs/screenshots/",
                "tmp" + "_browser_agent",
            )
            os.makedirs(screenshot_dir, exist_ok=True)
            self.screenshot_dir = screenshot_dir

    async def reply(
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """
        Process a message and return a response.

        Args:
            msg (`Msg | list[Msg] | None`, optional):
                The input message(s) to the agent.
            structured_model (`Type[BaseModel] | None`, optional):
                The required structured output model. If provided, the agent
                is expected to generate structured output in the `metadata`
                field of the output message.

        Returns:
            Msg: The response message.
        """
        self.init_query = (
            msg.content
            if isinstance(msg, Msg)
            else msg[0].content
            if isinstance(msg, list)
            else ""
        )
        if self.start_url and not self._has_initial_navigated:
            await self._navigate_to_start_url()
            self._has_initial_navigated = True

        # Choose between default reply and DFS reply based on the parameter
        if self.use_dfs_reply:
            return await self._reply_with_dfs(msg, structured_model)
        else:
            return await self._default_reply(msg, structured_model)

    async def _default_reply(
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """Default reply method using the original reasoning-acting loop."""
        msg = await self._task_decomposition_and_reformat(msg)
        # original reply function
        await self.memory.add(msg)
        self._required_structured_model = structured_model
        # Record structured output model if provided
        if structured_model:
            self.toolkit.set_extended_model(
                self.finish_function_name,
                structured_model,
            )
        # The reasoning-acting loop
        reply_msg = None
        for iter_n in range(self.max_iters):
            self.iter_n = iter_n + 1
            await self._summarize_mem()
            observe_msg = await self._build_observation()
            msg_reasoning = await self._reasoning(observe_msg)

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
        if not reply_msg:
            await self._summarizing()

        await self.memory.add(reply_msg)
        return reply_msg

    async def _reply_with_dfs(
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """DFS reply method using the search-based approach."""
        msg = await self._task_decomposition_and_reformat(msg)
        await self.memory.add(msg)
        self._required_structured_model = structured_model

        # Record structured output model if provided
        if structured_model:
            self.toolkit.set_extended_model(
                self.finish_function_name,
                structured_model,
            )

        # Use the DFS method from BrowserSearchMixin
        return await BrowserSearchMixin._reply_with_dfs(self, msg)

    async def _reasoning(
        self,
        observe_msg: Msg | None = None,
    ) -> Msg:
        """Perform the reasoning process."""
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", self.sys_prompt, "system"),
                *await self.memory.get_memory(),
                observe_msg,
            ],
        )

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

            return msg

        except asyncio.CancelledError as e:
            interrupted_by_user = True
            raise e from None

        finally:
            await self.memory.add(msg)
            await self._update_chunk_observation_status(
                output_msg=msg,
            )
            # Post-process for user interruption
            if interrupted_by_user and msg:
                # Fake tool results
                tool_use_blocks: list = (
                    msg.get_content_blocks(  # pylint: disable=E1133
                        "tool_use",
                    )
                )
                for tool_call in tool_use_blocks:  # pylint: disable=E1133
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

    async def _summarize_mem(
        self,
    ) -> None:
        """Summarize memory if too long"""
        mem_len = await self.memory.size()
        if mem_len > self.max_memory_length:
            await self._memory_summarizing()

    async def _build_observation(
        self,
    ) -> Msg:
        """Get a snapshot in text before reasoning"""

        image_path: Optional[str] = None
        if (
            self.model.model_name.startswith("qvq")
            or "-vl" in self.model.model_name
        ):
            # If the model supports multimodal input, take a screenshot
            # and pass it to the observation message
            img_path = os.path.join(
                self.screenshot_dir,
                f"screenshot_{self.iter_n}.png",
            )
            # if the img_path already exists,
            # do not need to take a screenshot again
            if not os.path.exists(img_path):
                image_path = await self._get_screenshot(img_path)
        if not self.chunk_continue_status:
            self.snapshot_in_chunk = await self._get_snapshot_in_text()

        observe_msg = self.observe_by_chunk(image_path)
        return observe_msg

    async def _update_chunk_observation_status(
        self,
        output_msg: Msg | None = None,
    ) -> None:
        """Update the chunk observation status after reasoning."""

        for _, b in enumerate(output_msg.content):
            if b["type"] == "text":
                # obtain response content
                raw_response = b["text"]
                # parse the response content to check if
                # it contains "REASONING_FINISHED"
                try:
                    if "```json" in raw_response:
                        raw_response = raw_response.replace(
                            "```json",
                            "",
                        ).replace("```", "")
                    data = json.loads(raw_response)
                    information = data.get("INFORMATION", "")
                    self.chunk_continue_status = data.get("STATUS", "CONTINUE")
                except Exception:
                    information = raw_response
                    if (
                        self.snapshot_chunk_id
                        < len(self.snapshot_in_chunk) - 1
                    ):
                        self.chunk_continue_status = True
                        self.snapshot_chunk_id += 1
                    else:
                        self.chunk_continue_status = False

                if not isinstance(information, str):
                    try:
                        information = json.dumps(
                            information,
                            ensure_ascii=False,
                        )
                    except Exception:
                        information = str(information)

                self.previous_chunkwise_information += (
                    f"Information in chunk {self.snapshot_chunk_id+1} "
                    f"of {len(self.snapshot_in_chunk)}:\n" + information + "\n"
                )

            if b["type"] == "tool_use":
                self.chunk_continue_status = False
                self.snapshot_chunk_id = 0
                self.previous_chunkwise_information = ""
                self.snapshot_in_chunk = []

    async def _acting(self, tool_call: ToolUseBlock) -> Msg | None:
        """Perform the acting process.

        Args:
            tool_call (`ToolUseBlock`):
                The tool use block to be executed.

        Returns:
            `Union[Msg, None]`:
                Return a message to the user if the `_finish_function` is
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
                    and not chunk.metadata.get("success")
                ):
                    await self.print(tool_res_msg, chunk.is_last)

                # Return message if generate_response is called successfully
                if tool_call[
                    "name"
                ] == self.finish_function_name and chunk.metadata.get(
                    "success",
                    True,
                ):
                    response_msg = chunk.metadata.get("response_msg")

            return response_msg

        finally:
            # Record the tool result message in the memory
            tool_res_msg = self._clean_tool_excution_content(tool_res_msg)
            if tool_call["name"] == "browser_subtask_manager":
                # remove the last tool call
                mem_len = await self.memory.size()
                if mem_len >= 1:
                    await self.memory.delete(mem_len - 1)
            else:
                await self.memory.add(tool_res_msg)

    def _clean_tool_excution_content(
        self,
        output_msg: Msg,
    ) -> Msg:
        """
        Hook func for cleaning the messy return after action.
        Observation will be done before reasoning steps.
        """

        for i, b in enumerate(output_msg.content):
            if b["type"] == "tool_result":
                for j, return_json in enumerate(b.get("output", [])):
                    if isinstance(return_json, dict) and "text" in return_json:
                        output_msg.content[i]["output"][j][
                            "output"
                        ] = self._filter_execution_text(return_json["text"])
                        output_msg.content[i]["output"][j][
                            "text"
                        ] = self._filter_execution_text(return_json["text"])
        return output_msg

    async def _task_decomposition_and_reformat(
        self,
        original_task: (
            Msg | list[Msg] | None
        ),  # Added type annotation for the argument
    ) -> Msg:  # Added return type annotation
        """Decompose the original task into smaller tasks and reformat it.

        Args:
            original_task (Msg | list[Msg] | None):
            The original task to be decomposed.

        Returns:
            Msg: The reformatted task with subtasks.
        """

        # Format the prompt with the original task and tools information

        if isinstance(original_task, list):
            original_task = original_task[0]

        prompt = await self.formatter.format(
            msgs=[
                Msg(
                    name="user",
                    content=self.task_decomposition_prompt.format(
                        start_url=self.start_url,
                        browser_agent_sys_prompt=self.sys_prompt,
                        original_task=original_task.content,
                    ),
                    role="user",
                ),
            ],
        )

        # Call the language model to get the decomposed tasks
        res = await self.model(
            prompt,
        )
        if self.model.stream:
            async for content_chunk in res:
                res_text = content_chunk.content[0]["text"]
        else:
            res_text = res.content[0]["text"]

        subtasks = []
        try:
            if "```json" in res_text:
                res_text = res_text.replace("```json", "")
                res_text = res_text.replace("```", "")
            subtasks = json.loads(res_text)
            if not isinstance(subtasks, list):
                subtasks = []
        except Exception:
            subtasks = [original_task.content]

        self.subtasks = subtasks
        self.current_subtask_idx = 0
        self.current_subtask = self.subtasks[0] if self.subtasks else None
        self.original_task = original_task.content
        # Return the decomposed tasks
        formatted_task = "The original task is: " + self.original_task + "\n"
        try:
            formatted_task += (
                "The decomposed subtasks are: "
                + json.dumps(self.subtasks)
                + "\n"
            )
            formatted_task += (
                "use the decomposed subtasks to complete the original task.\n"
            )
        except Exception:
            pass

        formatted_task = Msg(
            name=original_task.name,
            content=formatted_task,
            role=original_task.role,
        )
        return formatted_task

    async def _navigate_to_start_url(self, url: str = None) -> None:
        """
        Navigate to the specified start URL using the browser_navigate tool.

        This method is automatically called during the first interaction to
        navigate to the configured start URL. It executes the browser
        navigation tool and processes the response to ensure the
        initial page is loaded.

        Returns:
            None
        """

        if url is None:
            url = self.start_url

        tool_call = ToolUseBlock(
            id=str(uuid.uuid4()),
            type="tool_use",
            name="browser_navigate",
            input={"url": url},
        )

        # Execute the navigation tool
        await self.toolkit.call_tool_function(tool_call)

    async def _get_snapshot_in_text(self) -> list:
        """Capture a text-based snapshot of the current webpage content.

        This method uses the browser_snapshot tool to retrieve the current
        webpage content in text format, which is used during the reasoning
        phase to provide context about the current browser state.

        Returns:
            list: A list of text chunks representing the current,
            webpage content, including elements, structure,
            and visible text.

        Note:
            This method is called automatically during the reasoning phase and
            provides essential context for decision-making about next actions.
        """
        snapshot_tool_call = ToolUseBlock(
            type="tool_use",
            id=str(uuid.uuid4()),  # Generate a unique ID for the tool call
            name="browser_snapshot",
            input={},  # No parameters required for this tool
        )
        snapshot_response = await self.toolkit.call_tool_function(
            snapshot_tool_call,
        )
        snapshot_str = ""
        async for chunk in snapshot_response:
            snapshot_str = chunk.content[0]["text"]
        snapshot_in_chunk = self._split_snapshot_by_chunk(
            snapshot_str,
        )

        return snapshot_in_chunk

    async def _memory_summarizing(self) -> None:
        """Summarize the current memory content to prevent context overflow.

        This method is called periodically to condense the conversation history
        by generating a summary of progress and maintaining only essential
        information. It preserves the initial user question and creates a
        concise summary of what has been accomplished and what remains to be
        done.

        Returns:
            None

        Note:
            This method is automatically called every 10 iterations to manage
            memory usage and maintain context relevance. The summarization
            helps prevent token limit issues while preserving important task
            context.
        """
        # Extract the initial user question
        initial_question = None
        memory_msgs = await self.memory.get_memory()
        for msg in memory_msgs:
            if msg.role == "user":
                initial_question = msg.content
                break

        # Generate a summary of the current progress
        hint_msg = Msg(
            "user",
            (
                "Summarize the current progress and outline the next steps "
                "for this task. Your summary should include:\n"
                "1. What has been completed so far.\n"
                "2. What key information has been found.\n"
                "3. What remains to be done.\n"
                "Ensure that your summary is clear, concise, and t"
                "hat no tasks are repeated or skipped."
            ),
            role="user",
        )

        # Format the prompt for the model
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", self.sys_prompt, "system"),
                *memory_msgs,
                hint_msg,
            ],
        )

        # Call the model to generate the summary
        res = await self.model(prompt)

        # Handle response
        summary_text = ""
        if self.model.stream:
            async for content_chunk in res:
                summary_text = content_chunk.content[0]["text"]
        else:
            summary_text = res.content[0]["text"]

        # Update the memory with the summarized content
        summarized_memory = []
        if initial_question:
            summarized_memory.append(
                Msg("user", initial_question, role="user"),
            )
        summarized_memory.append(
            Msg(self.name, summary_text, role="assistant"),
        )

        # Clear and reload memory
        await self.memory.clear()
        for msg in summarized_memory:
            await self.memory.add(msg)

    async def _get_screenshot(self, img_path: str = "") -> Optional[str]:
        """
        Optionally take a screenshot of the current web page
        for use in multimodal prompts.
        Returns the path to the image if available, else None.
        """
        try:
            # Prepare tool call for screenshot
            tool_call = ToolUseBlock(
                id=str(uuid.uuid4()),
                name="browser_take_screenshot",
                input={},
                type="tool_use",
            )
            # Execute tool call via service toolkit
            screenshot_response = await self.toolkit.call_tool_function(
                tool_call,
            )
            # Extract image path from response
            async for chunk in screenshot_response:
                if (
                    chunk.content
                    and len(chunk.content) > 1
                    and "source" in chunk.content[1]
                ):
                    image_data = chunk.content[1]["source"]["data"]
                    image_data = base64.b64decode(image_data)
                    with open(img_path, "wb") as fi:
                        fi.write(image_data)
                    returned_img_path = img_path
                    # Exit loop on success
                else:
                    returned_img_path = None

        except Exception:
            returned_img_path = None
        return returned_img_path

    @staticmethod
    def _filter_execution_text(
        text: str,
        keep_page_state: bool = False,
    ) -> str:
        """
        Filter and clean browser tool execution output to remove verbose
        content.

        This utility method removes unnecessary verbose content from browser
        tool responses, including JavaScript code blocks, console messages,
        and YAML content that can overwhelm the context window without
        providing useful information.

        Args:
            text (str):
                The raw execution text from browser tools that
                needs to be filtered.
            keep_page_state (bool, optional):
                Whether to preserve page state information
                including URL and YAML content. Defaults to False.

        Returns:
            str: The filtered execution text.
        """
        if not keep_page_state:
            # Remove Page Snapshot and YAML content
            text = re.sub(r"- Page URL.*", "", text, flags=re.DOTALL)
            text = re.sub(r"```yaml.*?```", "", text, flags=re.DOTALL)
        # # Remove JavaScript code blocks

        # Remove console messages section that can be very verbose
        # (between "### New console messages" and "### Page state")
        text = re.sub(
            r"### New console messages.*?(?=### Page state)",
            "",
            text,
            flags=re.DOTALL,
        )
        # Trim leading/trailing whitespace
        return text.strip()

    def _split_snapshot_by_chunk(
        self,
        snapshot_str: str,
        max_length: int = 80000,
    ) -> list[str]:
        self.snapshot_chunk_id = 0
        return [
            snapshot_str[i : i + max_length]
            for i in range(0, len(snapshot_str), max_length)
        ]

    def observe_by_chunk(self, image_path: str | None = "") -> Msg:
        """Create an observation message for chunk-based reasoning.

        This method formats the current chunk of the webpage snapshot with
        contextual information from previous chunks to create a structured
        observation message for the reasoning phase.

        Returns:
            Msg: A user message containing the formatted reasoning prompt
                with chunk information and context from previous chunks.
        """
        reasoning_prompt = self.reasoning_prompt.format(
            previous_chunkwise_information=self.previous_chunkwise_information,
            current_subtask=self.current_subtask,
            i=self.snapshot_chunk_id + 1,
            total_pages=len(self.snapshot_in_chunk),
            chunk=self.snapshot_in_chunk[self.snapshot_chunk_id],
            init_query=self.init_query,
        )
        content = [
            TextBlock(
                type="text",
                text=reasoning_prompt,
            ),
        ]
        if (
            self.model.model_name.startswith("qvq")
            or "-vl" in self.model.model_name
        ):
            if image_path:
                image_block = {
                    "type": "image",
                    "source": {
                        "type": "url",
                        "url": image_path,
                    },
                }
                content.append(image_block)

        observe_msg = Msg(
            "user",
            content=content,
            role="user",
        )
        return observe_msg

    async def browser_subtask_manager(self) -> ToolResponse:
        """
        Determine whether the current subtask is completed.
        This tool should only be used when it is believed that
         the current subtask is done.

        Returns:
            `ToolResponse`:
                If completed, advance current_subtask_idx;
                otherwise, leave it unchanged.
        """
        if (
            not hasattr(self, "subtasks")
            or not self.subtasks
            or self.current_subtask is None
        ):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"Tool call Error. Cannot be executed. "
                            f"Current subtask remains: {self.current_subtask}"
                        ),
                    ),
                ],
            )

        # take memory as context
        memory_content = await self.memory.get_memory()

        # LLM prompt for subtask validation
        sys_prompt = (
            "You are an expert in subtask validation. \n"
            "Given the following subtask and the agent's"
            " recent memory, strictly judge if the subtask "
            "is FULLY completed. \n"
            "If yes, reply ONLY 'SUBTASK_COMPLETED'. "
            "If not, reply ONLY 'SUBTASK_NOT_COMPLETED'."
        )
        if len(self.snapshot_in_chunk) > 0:
            user_prompt = (
                f"Subtask: {self.current_subtask}\n"
                f"Recent memory:\n{[str(m) for m in memory_content[-10:]]}\n"
                f"Current page:\n{self.snapshot_in_chunk[0]}"
            )
        else:
            user_prompt = (
                f"Subtask: {self.current_subtask}\n"
                f"Recent memory:\n{[str(m) for m in memory_content[-10:]]}\n"
            )
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", sys_prompt, role="system"),
                Msg("user", user_prompt, role="user"),
            ],
        )

        response = await self.model(prompt)
        response_text = ""
        if self.model.stream:
            # If the model supports streaming, collect chunks
            async for chunk in response:
                response_text += chunk.content[0]["text"]
        else:
            # If not streaming, get the full response at once
            response_text = response.content[0]["text"]

        if "SUBTASK_COMPLETED" in response_text.strip().upper():
            self.current_subtask_idx += 1
            if self.current_subtask_idx < len(self.subtasks):
                self.current_subtask = str(
                    self.subtasks[self.current_subtask_idx],
                )
            else:
                self.current_subtask = None
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            "Tool call SUCCESS."
                            " Current subtask updates to: "
                            f"{self.current_subtask}"
                        ),
                    ),
                ],
            )

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "Tool call SUCCESS."
                        f" Current subtask remains: {self.current_subtask}"
                    ),
                ),
            ],
        )

    async def browser_generate_final_response(
        self,
        **kwargs: Any,
    ) -> ToolResponse:
        """Generate a response when the agent has completed all subtasks."""
        hint_msg = Msg(
            "user",
            _BROWSER_AGENT_SUMMARIZE_TASK_PROMPT,
            role="user",
        )
        memory_msgs = await self.memory.get_memory()
        last_msg = memory_msgs[-1]
        # check if the last message has tool call

        last_msg.content = last_msg.get_content_blocks("text")
        memory_msgs[-1] = last_msg

        # Generate a reply by summarizing the current situation
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", self.sys_prompt, "system"),
                *memory_msgs,
                hint_msg,
            ],
        )
        try:
            res = await self.model(prompt)
            res_msg = Msg(
                "assistant",
                [],
                "assistant",
            )
            if self.model.stream:
                async for content_chunk in res:
                    summary_text = content_chunk.content[0]["text"]
            else:
                summary_text = res.content[0]["text"]

            res_msg.content = summary_text
            await self.print(res_msg, True)
            if self._required_structured_model:
                try:
                    # Use the metadata field of the message to store the
                    # structured output
                    res_msg.metadata = (
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
                    "response_msg": res_msg,
                },
                is_last=True,
            )
        except Exception as e:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Tool call Error. Cannot be executed. {e}",
                    ),
                ],
                metadata={"success": False},
                is_last=True,
            )
