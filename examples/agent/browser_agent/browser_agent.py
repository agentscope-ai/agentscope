#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser Agent"""
# flake8: noqa: E501
# pylint: disable=W0212,too-many-lines,C0301,W0107,C0411

import re
import uuid
import os
import json
import inspect
from functools import wraps
from typing import Type, Optional, Any, Literal
import asyncio
import copy

from pydantic import BaseModel

from agentscope.agent import ReActAgent
from agentscope._logging import logger
from agentscope.formatter import FormatterBase
from agentscope.memory import MemoryBase
from agentscope.message import (
    Msg,
    ToolUseBlock,
    TextBlock,
    ToolResultBlock,
    ImageBlock,
    Base64Source,
)
from agentscope.model import ChatModelBase
from agentscope.tool import (
    Toolkit,
    ToolResponse,
)
from agentscope.token import TokenCounterBase, OpenAITokenCounter

from build_in_helper._image_understanding import image_understanding
from build_in_helper._video_understanding import video_understanding
from build_in_helper._file_download import file_download
from build_in_helper._form_filling import form_filling

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROMPT_DIR = os.path.join(_CURRENT_DIR, "build_in_prompt")
_HELPER_DIR = os.path.join(_CURRENT_DIR, "build_in_helper")


class EmptyModel(BaseModel):
    """Empty structured model for default structured output requirement."""

    pass


with open(
    os.path.join(_PROMPT_DIR, "browser_agent_sys_prompt.md"),
    "r",
    encoding="utf-8",
) as f:
    _BROWSER_AGENT_DEFAULT_SYS_PROMPT = f.read()

with open(
    os.path.join(_PROMPT_DIR, "browser_agent_pure_reasoning_prompt.md"),
    "r",
    encoding="utf-8",
) as f:
    _BROWSER_AGENT_DEFAULT_PURE_REASONING_PROMPT = f.read()

with open(
    os.path.join(_PROMPT_DIR, "browser_agent_observe_reasoning_prompt.md"),
    "r",
    encoding="utf-8",
) as f:
    _BROWSER_AGENT_DEFAULT_OBSERVE_REASONING_PROMPT = f.read()

with open(
    os.path.join(_PROMPT_DIR, "browser_agent_task_decomposition_prompt.md"),
    "r",
    encoding="utf-8",
) as f:
    _BROWSER_AGENT_DEFAULT_TASK_DECOMPOSITION_PROMPT = f.read()

with open(
    os.path.join(_PROMPT_DIR, "browser_agent_summarize_task.md"),
    "r",
    encoding="utf-8",
) as f:
    _BROWSER_AGENT_SUMMARIZE_TASK_PROMPT = f.read()


class BrowserAgent(ReActAgent):
    """
    Browser Agent that extends ReActAgent with browser-specific capabilities.

    The agent leverages MCP servers to access browser tools with Playwright,
    enabling sophisticated web automation tasks.
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
        pure_reasoning_prompt: str = _BROWSER_AGENT_DEFAULT_PURE_REASONING_PROMPT,
        observe_reasoning_prompt: str = _BROWSER_AGENT_DEFAULT_OBSERVE_REASONING_PROMPT,
        task_decomposition_prompt: str = _BROWSER_AGENT_DEFAULT_TASK_DECOMPOSITION_PROMPT,
        token_counter: TokenCounterBase = OpenAITokenCounter("gpt-4o"),
        max_mem_length: int = 20,
    ) -> None:
        """Initialize the Browser Agent."""
        self.start_url = start_url
        self._has_initial_navigated = False
        self.pure_reasoning_prompt = pure_reasoning_prompt
        self.observe_reasoning_prompt = observe_reasoning_prompt
        self.task_decomposition_prompt = task_decomposition_prompt
        self.max_memory_length = max_mem_length
        self.token_estimator = token_counter
        self.snapshot_chunk_id = 0
        self.chunk_continue_status = False
        self.previous_chunkwise_information = ""
        self.snapshot_in_chunk: list[str] = []
        self.subtasks: list[Any] = []
        self.original_task = ""
        self.current_subtask_idx = 0
        self.current_subtask: Any = None
        self.iter_n = 0
        self.finish_function_name = "browser_generate_final_response"
        self.init_query = ""
        self._required_structured_model: Type[BaseModel] | None = None

        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            memory=memory,
            toolkit=toolkit,
            max_iters=max_iters,
        )

        # Register tools
        self.toolkit.register_tool_function(self.browser_subtask_manager)

        # Register skill tools if model supports multimodal
        if self._supports_multimodal():
            self._register_skill_tool(image_understanding)
            self._register_skill_tool(video_understanding)

        # Register other skill tools
        self._register_skill_tool(file_download)
        self._register_skill_tool(form_filling)

        # Build a tool list without screenshot to avoid unnecessary captures
        self.no_screenshot_tool_list = [
            tool
            for tool in self.toolkit.get_json_schemas()
            if tool.get("function", {}).get("name")
            != "browser_take_screenshot"
        ]

    async def reply(  # pylint: disable=R0912,R0915
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """Process a message and return a response."""
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

        msg = await self._task_decomposition_and_reformat(msg)

        await self.memory.add(msg)
        # Default to EmptyModel to require structured output if none provided
        if structured_model is None:
            structured_model = EmptyModel

        tool_choice: Literal["auto", "none", "required"] | None = None
        self._required_structured_model = structured_model

        # Register finish tool only when structured model is required
        if structured_model:
            if self.finish_function_name not in self.toolkit.tools:
                self.toolkit.register_tool_function(
                    getattr(self, self.finish_function_name),
                )
            self.toolkit.set_extended_model(
                self.finish_function_name,
                structured_model,
            )
            tool_choice = "required"
        else:
            self.toolkit.remove_tool_function(self.finish_function_name)

        # The reasoning-acting loop
        structured_output = None
        reply_msg = None
        for iter_n in range(self.max_iters):
            self.iter_n = iter_n + 1
            await self._summarize_mem()
            msg_reasoning = await self._pure_reasoning(tool_choice)

            tool_calls = msg_reasoning.get_content_blocks("tool_use")
            if tool_calls and tool_calls[0]["name"] == "browser_snapshot":
                msg_reasoning = await self._reasoning_with_observation()

            futures = [
                self._acting(tool_call)
                for tool_call in msg_reasoning.get_content_blocks("tool_use")
            ]

            # Parallel tool calls or not
            if self.parallel_tool_calls:
                structured_outputs = await asyncio.gather(*futures)
            else:
                structured_outputs = [await _ for _ in futures]

            # Check for exit condition
            # If structured output is still not satisfied
            if self._required_structured_model:
                # Remove None results
                structured_outputs = [_ for _ in structured_outputs if _]

                msg_hint = None
                # If the acting step generates structured outputs
                if structured_outputs:
                    # Cache the structured output data
                    structured_output = structured_outputs[-1]

                    reply_msg = Msg(
                        self.name,
                        structured_output.get("subtask_progress_summary", ""),
                        "assistant",
                        metadata=structured_output,
                    )
                    break

                if not msg_reasoning.has_content_blocks("tool_use"):
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
                    await self._reasoning_hint_msgs.add(msg_hint)
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

        return reply_msg

    async def _pure_reasoning(
        self,
        tool_choice: Literal["auto", "none", "required"] | None = None,
    ) -> Msg:
        """Initial reasoning without screenshot observation."""
        msg = Msg(
            "user",
            content=self.pure_reasoning_prompt.format(
                current_subtask=self.current_subtask,
                init_query=self.original_task,
            ),
            role="user",
        )
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", self.sys_prompt, "system"),
                *await self.memory.get_memory(),
                msg,
                # The hint messages to guide the agent's behavior, maybe empty
                *await self._reasoning_hint_msgs.get_memory(),
            ],
        )

        # Clear the hint messages after use
        await self._reasoning_hint_msgs.clear()

        res = await self.model(
            prompt,
            tools=self.no_screenshot_tool_list,
            tool_choice=tool_choice,
        )

        interrupted_by_user = False
        msg = None
        try:
            if self.model.stream:
                msg = Msg(self.name, [], "assistant")
                async for content_chunk in res:
                    msg.content = content_chunk.content
                await self.print(msg)
            else:
                msg = Msg(self.name, list(res.content), "assistant")
                await self.print(msg)
            return msg
        except asyncio.CancelledError as e:
            interrupted_by_user = True
            raise e from None
        finally:
            await self.memory.add(msg)
            tool_use_blocks: list = msg.get_content_blocks("tool_use")  # type: ignore
            if interrupted_by_user and msg:
                for tool_call in tool_use_blocks:  # pylint: disable=E1133
                    msg_res = Msg(
                        "system",
                        [
                            ToolResultBlock(
                                type="tool_result",
                                id=tool_call["id"],
                                name=tool_call["name"],
                                output="The tool call has been interrupted by the user.",
                            ),
                        ],
                        "system",
                    )
                    await self.memory.add(msg_res)
                    await self.print(msg_res)

    async def _reasoning_with_observation(self) -> Msg:
        """Perform the reasoning process with page observation in chunks."""
        self.snapshot_chunk_id = 0
        self.chunk_continue_status = False
        self.previous_chunkwise_information = ""
        self.snapshot_in_chunk = []

        mem_len = await self.memory.size()
        await self.memory.delete(mem_len - 1)

        self.snapshot_in_chunk = await self._get_snapshot_in_text()
        for _ in self.snapshot_in_chunk:
            observe_msg = await self._build_observation()
            prompt = await self.formatter.format(
                msgs=[
                    Msg("system", self.sys_prompt, "system"),
                    *await self.memory.get_memory(),
                    observe_msg,
                ],
            )
            res = await self.model(
                prompt,
                tools=self.no_screenshot_tool_list,
            )

            interrupted_by_user = False
            msg = None
            try:
                if self.model.stream:
                    msg = Msg(self.name, [], "assistant")
                    async for content_chunk in res:
                        msg.content = content_chunk.content
                    # await self.print(msg)
                else:
                    msg = Msg(self.name, list(res.content), "assistant")
                    # await self.print(msg)
                logger.info(msg.content)
            except asyncio.CancelledError as e:
                interrupted_by_user = True
                raise e from None

            tool_use_blocks: list = msg.get_content_blocks("tool_use")  # type: ignore
            await self._update_chunk_observation_status(output_msg=msg)

            if interrupted_by_user and msg:
                for tool_call in tool_use_blocks:  # pylint: disable=E1133
                    msg_res = Msg(
                        "system",
                        [
                            ToolResultBlock(
                                type="tool_result",
                                id=tool_call["id"],
                                name=tool_call["name"],
                                output="The tool call has been interrupted by the user.",
                            ),
                        ],
                        "system",
                    )
                    await self.memory.add(msg_res)
                    await self.print(msg_res)

            if not self.chunk_continue_status:
                break

        await self.memory.add(msg)
        return msg

    async def _summarize_mem(self) -> None:
        """Summarize memory if too long."""
        mem_len = await self.memory.size()
        if mem_len > self.max_memory_length:
            await self._memory_summarizing()

    async def _build_observation(self) -> Msg:
        """Get a snapshot (and optional screenshot) before reasoning."""
        image_data: Optional[str] = None
        if self._supports_multimodal():
            image_data = await self._get_screenshot()
        observe_msg = self.observe_by_chunk(image_data)
        return observe_msg

    async def _update_chunk_observation_status(
        self,
        output_msg: Msg | None = None,
    ) -> None:
        """Update the chunk observation status after reasoning."""
        for _, b in enumerate(output_msg.content):
            if b["type"] == "text":
                raw_response = b["text"]
                try:
                    if "```json" in raw_response:
                        raw_response = raw_response.replace(
                            "```json",
                            "",
                        ).replace("```", "")
                    data = json.loads(raw_response)
                    information = data.get("INFORMATION", "")
                    # Continue unless STATUS is explicitly REASONING_FINISHED
                    self.chunk_continue_status = (
                        data.get("STATUS") != "REASONING_FINISHED"
                    )
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
                    f"Information in chunk {self.snapshot_chunk_id+1} of {len(self.snapshot_in_chunk)}:\n"
                    + information
                    + "\n"
                )
            if b["type"] == "tool_use":
                self.chunk_continue_status = False

    async def _acting(self, tool_call: ToolUseBlock) -> dict | None:
        """Perform the acting process and return structured output if generated."""
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
            tool_res = await self.toolkit.call_tool_function(tool_call)
            structured_output = None
            async for chunk in tool_res:
                tool_res_msg.content[0]["output"] = chunk.content  # type: ignore[index]
                await self.print(tool_res_msg, chunk.is_last)

                # Raise the CancelledError to handle the interruption
                if chunk.is_interrupted:
                    raise asyncio.CancelledError()

                # Return structured output if generate_response is called successfully
                if (
                    tool_call["name"] == self.finish_function_name
                    and chunk.metadata
                    and chunk.metadata.get("success", False)
                ):
                    # Only return the structured output
                    structured_output = chunk.metadata.get("structured_output")
                    return structured_output

            return None
        finally:
            tool_res_msg = self._clean_tool_excution_content(tool_res_msg)
            # Always add tool result to maintain message sequence integrity
            # DashScope requires every tool_call to have a corresponding tool_result
            # Don't delete assistant messages to avoid breaking message sequence
            await self.memory.add(tool_res_msg)

    def _clean_tool_excution_content(self, output_msg: Msg) -> Msg:
        """Clean verbose tool outputs before printing and storing."""
        for i, b in enumerate(output_msg.content):
            if b["type"] == "tool_result":
                for j, return_json in enumerate(b.get("output", [])):
                    if isinstance(return_json, dict) and "text" in return_json:
                        output_msg.content[i]["output"][j]["text"] = self._filter_execution_text(  # type: ignore[index]
                            return_json["text"],
                        )
        return output_msg

    async def _task_decomposition_and_reformat(
        self,
        original_task: Msg | list[Msg] | None,
    ) -> Msg:
        """Decompose the original task into smaller tasks and reformat."""
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
        res = await self.model(prompt)
        decompose_text = ""
        if self.model.stream:
            async for content_chunk in res:
                decompose_text = content_chunk.content[0]["text"]
        else:
            decompose_text = res.content[0]["text"]
        logger.info(decompose_text)

        reflection_prompt_path = os.path.join(
            _PROMPT_DIR,
            "browser_agent_decompose_reflection_prompt.md",
        )
        with open(reflection_prompt_path, "r", encoding="utf-8") as fj:
            decompose_reflection_prompt = fj.read()

        reflection_prompt = await self.formatter.format(
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
                Msg(
                    name="system",
                    content=decompose_text,
                    role="system",
                ),
                Msg(
                    name="user",
                    content=decompose_reflection_prompt.format(
                        original_task=original_task.content,
                        subtasks=decompose_text,
                    ),
                    role="user",
                ),
            ],
        )
        reflection_res = await self.model(reflection_prompt)
        reflection_text = ""
        if self.model.stream:
            async for content_chunk in reflection_res:
                reflection_text = content_chunk.content[0]["text"]
        else:
            reflection_text = reflection_res.content[0]["text"]
        logger.info(reflection_text)

        subtasks: list[Any] = []
        try:
            if "```json" in reflection_text:
                reflection_text = reflection_text.replace(
                    "```json",
                    "",
                ).replace("```", "")
            subtasks_json = json.loads(reflection_text)
            subtasks = subtasks_json.get("REVISED_SUBTASKS", [])
            if not isinstance(subtasks, list):
                subtasks = []
        except Exception:
            subtasks = [original_task.content]

        self.subtasks = subtasks
        self.current_subtask_idx = 0
        self.current_subtask = self.subtasks[0] if self.subtasks else None
        # Prefer text content extraction if available
        try:
            self.original_task = original_task.get_text_content()
        except Exception:
            self.original_task = original_task.content

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
        logger.info(  # pylint: disable=W1203
            f"The formatted task is: \n{formatted_task.content}",
        )
        return formatted_task

    async def _navigate_to_start_url(self) -> None:
        """Navigate to the start URL and clean up extra tabs."""
        tool_call = ToolUseBlock(
            id=str(uuid.uuid4()),
            name="browser_tabs",
            input={"action": "list"},
            type="tool_use",
        )
        response = await self.toolkit.call_tool_function(tool_call)
        response_text = ""
        async for chunk in response:
            # chunk.content might be a list[TextBlock]
            if chunk.content and "text" in chunk.content[0]:
                response_text = chunk.content[0]["text"]
        tab_numbers = re.findall(r"- (\d+):", response_text)
        for _ in tab_numbers[1:]:
            tool_call = ToolUseBlock(
                id=str(uuid.uuid4()),
                name="browser_tabs",
                input={"action": "close", "index": 0},
                type="tool_use",
            )
            await self.toolkit.call_tool_function(tool_call)

        tool_call = ToolUseBlock(
            id=str(uuid.uuid4()),
            type="tool_use",
            name="browser_navigate",
            input={"url": self.start_url},
        )
        await self.toolkit.call_tool_function(tool_call)

    async def _get_snapshot_in_text(self) -> list[str]:
        """Capture a text-based snapshot of the current webpage content."""
        snapshot_tool_call = ToolUseBlock(
            type="tool_use",
            id=str(uuid.uuid4()),
            name="browser_snapshot",
            input={},
        )
        snapshot_response = await self.toolkit.call_tool_function(
            snapshot_tool_call,
        )
        snapshot_str = ""
        async for chunk in snapshot_response:
            snapshot_str = chunk.content[0]["text"]
        snapshot_in_chunk = self._split_snapshot_by_chunk(snapshot_str)
        return snapshot_in_chunk

    async def _memory_summarizing(self) -> None:
        """Summarize the current memory content to prevent context overflow."""
        initial_question = None
        memory_msgs = await self.memory.get_memory()
        for msg in memory_msgs:
            if msg.role == "user":
                initial_question = msg.content
                break

        hint_msg = Msg(
            "user",
            (
                "Summarize the current progress and outline the next steps for this task. "
                "Your summary should include:\n"
                "1. What has been completed so far.\n"
                "2. What key information has been found.\n"
                "3. What remains to be done.\n"
                "Ensure that your summary is clear, concise, and that no tasks are repeated or skipped."
            ),
            role="user",
        )
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", self.sys_prompt, "system"),
                *memory_msgs,
                hint_msg,
            ],
        )
        res = await self.model(prompt)
        summary_text = ""
        print_msg = Msg(name=self.name, content=[], role="assistant")
        if self.model.stream:
            async for content_chunk in res:
                summary_text = content_chunk.content[0]["text"]
                print_msg.content = content_chunk.content
                await self.print(print_msg, last=False)
        else:
            summary_text = res.content[0]["text"]
        print_msg.content = [TextBlock(type="text", text=summary_text)]
        await self.print(print_msg, last=True)

        summarized_memory: list[Msg] = []
        if initial_question:
            summarized_memory.append(
                Msg("user", initial_question, role="user"),
            )
        summarized_memory.append(
            Msg(self.name, summary_text, role="assistant"),
        )
        await self.memory.clear()
        for m in summarized_memory:
            await self.memory.add(m)

    async def _get_screenshot(self) -> Optional[str]:
        """
        Optionally take a screenshot of the current web page for multimodal prompts.
        Returns base64-encoded PNG data if available, else None.
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
            # Extract image base64 from response
            async for chunk in screenshot_response:
                if (
                    chunk.content
                    and len(chunk.content) > 1
                    and "data" in chunk.content[1]
                ):
                    image_data = chunk.content[1]["data"]
                else:
                    image_data = None
        except Exception:
            image_data = None
        return image_data

    @staticmethod
    def _filter_execution_text(
        text: str,
        keep_page_state: bool = False,
    ) -> str:
        """Filter and clean browser tool execution output to remove verbosity."""
        if not keep_page_state:
            text = re.sub(r"- Page URL.*", "", text, flags=re.DOTALL)
            text = re.sub(r"```yaml.*?```", "", text, flags=re.DOTALL)
        text = re.sub(
            r"### New console messages.*?(?=### Page state)",
            "",
            text,
            flags=re.DOTALL,
        )
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

    def observe_by_chunk(self, image_data: str | None = "") -> Msg:
        """Create an observation message for chunk-based reasoning."""
        reasoning_prompt = self.observe_reasoning_prompt.format(
            previous_chunkwise_information=self.previous_chunkwise_information,
            current_subtask=self.current_subtask,
            i=self.snapshot_chunk_id + 1,
            total_pages=len(self.snapshot_in_chunk),
            chunk=self.snapshot_in_chunk[self.snapshot_chunk_id],
            init_query=self.original_task,
        )
        content: list[Any] = [TextBlock(type="text", text=reasoning_prompt)]
        if self._supports_multimodal():
            if image_data:
                image_block = ImageBlock(
                    type="image",
                    source=Base64Source(
                        type="base64",
                        media_type="image/png",
                        data=image_data,
                    ),
                )
                content.append(image_block)
        observe_msg = Msg("user", content=content, role="user")
        return observe_msg

    async def browser_subtask_manager(  # pylint: disable=R0912,R0915
        self,
    ) -> ToolResponse:  # pylint: disable=R0912,R0915
        """Validate and advance current subtask if completed."""
        if (
            not hasattr(self, "subtasks")
            or not self.subtasks
            or self.current_subtask is None
        ):
            self.current_subtask = self.original_task
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"Tool call Error. Cannot be executed. Current subtask remains: {self.current_subtask}"
                        ),
                    ),
                ],
            )
        memory_content = await self.memory.get_memory()
        sys_prompt = (
            "You are an expert in subtask validation. \n"
            "Given the following subtask and the agent's recent memory, strictly judge if the subtask is FULLY completed. \n"
            "If yes, reply ONLY 'SUBTASK_COMPLETED'. If not, reply ONLY 'SUBTASK_NOT_COMPLETED'."
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
        print_msg = Msg(name=self.name, content=[], role="assistant")
        if self.model.stream:
            async for chunk in response:
                response_text += chunk.content[0]["text"]
                print_msg.content = chunk.content
                await self.print(print_msg, last=False)
        else:
            response_text = response.content[0]["text"]
        print_msg.content = [TextBlock(type="text", text=response_text)]
        await self.print(print_msg, last=True)

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
                            "Tool call SUCCESS. Current subtask updates to: "
                            f"{self.current_subtask}"
                        ),
                    ),
                ],
            )
        else:
            revise_prompt_path = os.path.join(
                _PROMPT_DIR,
                "browser_agent_subtask_revise_prompt.md",
            )
            with open(revise_prompt_path, "r", encoding="utf-8") as fr:
                revise_prompt = fr.read()
            memory_content = await self.memory.get_memory()
            user_prompt = revise_prompt.format(
                memory=[str(m) for m in memory_content[-10:]],
                subtasks=json.dumps(self.subtasks, ensure_ascii=False),
                current_subtask=str(self.current_subtask),
                original_task=str(self.original_task),
            )
            prompt = await self.formatter.format(
                msgs=[Msg("user", user_prompt, role="user")],
            )
            response = await self.model(prompt)
            if self.model.stream:
                async for chunk in response:
                    revise_text = chunk.content[0]["text"]
            else:
                revise_text = response.content[0]["text"]
            try:
                if "```json" in revise_text:
                    revise_text = revise_text.replace("```json", "").replace(
                        "```",
                        "",
                    )
                revise_json = json.loads(revise_text)
                if_revised = revise_json.get("IF_REVISED")
                if if_revised:
                    revised_subtasks = revise_json.get("REVISED_SUBTASKS", [])
                    if isinstance(revised_subtasks, list) and revised_subtasks:
                        self.subtasks = revised_subtasks
                        self.current_subtask_idx = 0
                        self.current_subtask = self.subtasks[0]
                        logger.info(
                            "Subtasks revised: %s, reason: %s",
                            self.subtasks,
                            revise_json.get("REASON", ""),
                        )
            except Exception as e:
                logger.warning("Failed to revise subtasks: %s", e)
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
        self,  # pylint: disable=W0613
        **kwargs: Any,  # pylint: disable=W0613
    ) -> ToolResponse:
        """Generate a final response; validate completion state."""
        hint_msg = Msg(
            "user",
            _BROWSER_AGENT_SUMMARIZE_TASK_PROMPT,
            role="user",
        )

        memory_msgs = await self.memory.get_memory()
        memory_msgs_copy = copy.deepcopy(memory_msgs)
        last_msg = memory_msgs_copy[-1]
        last_msg.content = last_msg.get_content_blocks("text")
        memory_msgs_copy[-1] = last_msg

        prompt = await self.formatter.format(
            msgs=[
                Msg("system", self.sys_prompt, "system"),
                *memory_msgs_copy,
                hint_msg,
            ],
        )
        try:
            res = await self.model(prompt)
            res_msg = Msg("assistant", [], "assistant")
            if self.model.stream:
                async for content_chunk in res:
                    summary_text = content_chunk.content[0]["text"]
            else:
                summary_text = res.content[0]["text"]
            if self.model.stream:
                summary_text = ""
                async for content_chunk in res:
                    res_msg.content = content_chunk.content
                    summary_text = content_chunk.content[0]["text"]
                    await self.print(res_msg, False)
                await self.print(res_msg, True)
            else:
                summary_text = res.content[0]["text"]
                res_msg.content = summary_text
                await self.print(res_msg, True)

            # Validate finish status
            finish_status = await self._validate_finish_status(summary_text)
            logger.info(  # pylint: disable=W1203
                f"Finish status: {finish_status}",
            )  # pylint: disable=W1203

            if "BROWSER_AGENT_TASK_FINISHED" in finish_status:
                structure_response = {
                    "task_done": True,
                    "subtask_progress_summary": summary_text,
                    "generated_files": {},
                }
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text="Successfully generated response.",
                        ),
                    ],
                    metadata={
                        "success": True,
                        "structured_output": structure_response,
                    },
                    is_last=True,
                )
            else:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=(
                                f"Here is a summary of current status:\n{summary_text}\nPlease continue.\n"
                                f"Following steps \n {finish_status}"
                            ),
                        ),
                    ],
                    metadata={"success": False, "structured_output": None},
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

    async def image_understanding(
        self,
        object_description: str,
        task: str,
    ) -> ToolResponse:
        """
        Locate an element by description, take a focused screenshot, and solve a task using it.
        """
        sys_prompt = (
            "You are a web page analysis expert. Given the following page snapshot and object description, "
            "identify the exact element and its reference string (ref) that matches the description. "
            'Return ONLY a JSON object: {"element": <element description>, "ref": <ref string>}'
        )
        snapshot_chunks = await self._get_snapshot_in_text()
        page_snapshot = snapshot_chunks[0] if snapshot_chunks else ""
        user_prompt = f"Object description: {object_description}\nPage snapshot:\n{page_snapshot}"
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", sys_prompt, role="system"),
                Msg("user", user_prompt, role="user"),
            ],
        )
        res = await self.model(prompt)
        if self.model.stream:
            async for chunk in res:
                model_text = chunk.content[0]["text"]
        else:
            model_text = res.content[0]["text"]
        try:
            if "```json" in model_text:
                model_text = model_text.replace("```json", "").replace(
                    "```",
                    "",
                )
            element_info = json.loads(model_text)
            element = element_info.get("element", "")
            ref = element_info.get("ref", "")
        except Exception:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="Failed to parse element/ref from model output.",
                    ),
                ],
                metadata={"success": False},
            )

        screenshot_tool_call = ToolUseBlock(
            id=str(uuid.uuid4()),
            name="browser_take_screenshot",
            input={"element": element, "ref": ref},
            type="tool_use",
        )
        screenshot_response = await self.toolkit.call_tool_function(
            screenshot_tool_call,
        )
        image_data = None
        async for chunk in screenshot_response:
            if chunk.content and len(chunk.content) > 1:
                block = chunk.content[1]
                if "data" in block:
                    image_data = block["data"]
                elif "source" in block and "data" in block["source"]:
                    image_data = block["source"]["data"]

        sys_prompt_task = (
            "You are a web automation expert. Given the object description, screenshot, and page context, "
            "solve the following task. Return ONLY the answer as plain text."
        )
        content_blocks: list[Any] = [
            TextBlock(
                type="text",
                text=f"Object description: {object_description}\nTask: {task}\nPage snapshot:\n{page_snapshot}",
            ),
        ]
        if image_data:
            image_block = ImageBlock(
                type="image",
                source=Base64Source(
                    type="base64",
                    media_type="image/png",
                    data=image_data,
                ),
            )
            content_blocks.append(image_block)
        prompt_task = await self.formatter.format(
            msgs=[
                Msg("system", sys_prompt_task, role="system"),
                Msg("user", content_blocks, role="user"),
            ],
        )
        res_task = await self.model(prompt_task)
        if self.model.stream:
            async for chunk in res_task:
                answer_text = chunk.content[0]["text"]
        else:
            answer_text = res_task.content[0]["text"]
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"Screenshot taken for element: {element}\nref: {ref}\n"
                        f"Task solution: {answer_text}"
                    ),
                ),
            ],
        )

    async def _validate_finish_status(self, summary: str) -> str:
        """Validate if the agent has completed its task based on the summary."""
        sys_prompt = (
            "You are an expert in task validation. "
            "Your job is to determine if the agent has completed its task"
            " based on the provided summary. If the summary is `NO_ANSWER`, this task "
            "is not over unless the task is determined as definitely not completed. "
            "If finished, strictly reply "
            '"BROWSER_AGENT_TASK_FINISHED" and your reason, otherwise return the remaining '
            "tasks or next steps."
        )
        initial_question = None
        memory_msgs = await self.memory.get_memory()
        for msg in memory_msgs:
            if msg.role == "user":
                initial_question = msg.content
                break
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", sys_prompt, role="system"),
                Msg(
                    "user",
                    content=(
                        "The initial task is to solve the following question: "
                        f"{initial_question} \n "
                        f"Here is a summary of current task completion process, please evaluate the task finish status.\n"
                        + summary
                    ),
                    role="user",
                ),
            ],
        )
        res = await self.model(prompt)
        response_text = ""
        if self.model.stream:
            async for content_chunk in res:
                response_text = content_chunk.content[0]["text"]
        else:
            response_text = res.content[0]["text"]
        return response_text

    def _register_skill_tool(
        self,
        skill_func: Any,
    ) -> None:
        """Bind the browser agent to a skill function and register it as a tool."""
        if asyncio.iscoroutinefunction(skill_func):

            @wraps(skill_func)
            async def tool(*args: Any, **kwargs: Any) -> Any:
                return await skill_func(
                    browser_agent=self,
                    *args,
                    **kwargs,
                )

        else:

            @wraps(skill_func)
            async def tool(*args: Any, **kwargs: Any) -> Any:
                return skill_func(
                    browser_agent=self,
                    *args,
                    **kwargs,
                )

        original_signature = inspect.signature(skill_func)
        parameters = list(original_signature.parameters.values())
        if parameters and parameters[0].name == "browser_agent":
            parameters = parameters[1:]
        try:
            tool.__signature__ = original_signature.replace(
                parameters=parameters,
            )
        except ValueError:
            pass

        self.toolkit.register_tool_function(tool)

    def _supports_multimodal(self) -> bool:
        """Check if the model supports multimodal input (images/videos)."""
        return (
            self.model.model_name.startswith("qvq")
            or "-vl" in self.model.model_name
            or "4o" in self.model.model_name
            or "gpt-5" in self.model.model_name
        )
