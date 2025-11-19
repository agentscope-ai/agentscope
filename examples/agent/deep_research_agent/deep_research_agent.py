# -*- coding: utf-8 -*-
"""Deep Research Agent"""
# pylint: disable=too-many-lines, no-name-in-module
import os
import json
import asyncio

from typing import Type, Optional, Any, Tuple
from datetime import datetime
from copy import deepcopy
import shortuuid
from pydantic import BaseModel

from built_in_prompt.promptmodule import (
    SubtasksDecomposition,
    WebExtraction,
    FollowupJudge,
    ReflectFailure,
)
from utils import (
    truncate_search_result,
    load_prompt_dict,
    get_dynamic_tool_call_json,
    get_structure_output,
)

from agentscope import logger, setup_logger
from agentscope.mcp import StatefulClientBase
from agentscope.agent import ReActAgent
from agentscope.model import ChatModelBase
from agentscope.formatter import FormatterBase
from agentscope.memory import MemoryBase
from agentscope.tool import (
    ToolResponse,
    view_text_file,
    write_text_file,
)
from agentscope.message import (
    Msg,
    ToolUseBlock,
    TextBlock,
    ToolResultBlock,
)


_DEEP_RESEARCH_AGENT_DEFAULT_SYS_PROMPT = "You're a helpful assistant."

_LOG_DIR = os.path.join(os.path.dirname(__file__), "log")
_LOG_PATH = os.path.join(
    _LOG_DIR,
    f"log_{datetime.now().strftime('%y%m%d%H%M%S')}.md",
)
os.makedirs(_LOG_DIR, exist_ok=True)
setup_logger(level="INFO", filepath=_LOG_PATH)


class SubTaskItem(BaseModel):
    """Subtask item of deep research agent."""

    objective: str
    working_plan: Optional[str] = None
    knowledge_gaps: Optional[str] = None


class DeepResearchAgent(ReActAgent):
    """
    Deep Research Agent for sophisticated research tasks.

    Example:
        .. code-block:: python

        agent = DeepResearchAgent(
            name="Friday",
            sys_prompt="You are a helpful assistant named Friday.",
            model=my_chat_model,
            formatter=my_chat_formatter,
            memory=InMemoryMemory(),
            search_mcp_client=my_tavily_search_client,
            tmp_file_storage_dir=agent_working_dir,
        )
        response = await agent(
            Msg(
                name=“user”,
                content="Please give me a survey of the LLM-empowered agent.",
                role=“user”
            )
        )
        ```
    """

    def __init__(
        self,
        name: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        memory: MemoryBase,
        search_mcp_client: StatefulClientBase,
        sys_prompt: str = _DEEP_RESEARCH_AGENT_DEFAULT_SYS_PROMPT,
        max_iters: int = 30,
        max_depth: int = 3,
        tmp_file_storage_dir: str = "tmp",
    ) -> None:
        """Initialize the Deep Research Agent.

        Args:
            name (str):
                The unique identifier name for the agent instance.
            model (ChatModelBase):
                The chat model used for generating responses and reasoning.
            formatter (FormatterBase):
                The formatter used to convert messages into the required
                format for the model API.
            memory (MemoryBase):
                The memory component used to store and retrieve dialogue
                history.
            search_mcp_client (StatefulClientBase):
                The mcp client used to provide the tools for deep search.
            sys_prompt (str, optional):
                The system prompt that defines the agent's behavior
                and personality.
                Defaults to _DEEP_RESEARCH_AGENT_DEFAULT_SYS_PROMPT.
            max_iters (int, optional):
                The maximum number of reasoning-acting loop iterations.
                Defaults to 30.
            max_depth (int, optional):
                The maximum depth of query expansion during deep searching.
                Defaults to 3.
            tmp_file_storage_dir (str, optional):
                The storage dir for generated files.
                Default to 'tmp'
        Returns:
            None
        """

        # initialization of prompts
        self.prompt_dict = load_prompt_dict()

        # Enhance the system prompt for deep research agent
        add_note = self.prompt_dict["add_note"].format_map(
            {"finish_function_name": f"`{self.finish_function_name}`"},
        )
        tool_use_rule = self.prompt_dict["tool_use_rule"].format_map(
            {"tmp_file_storage_dir": tmp_file_storage_dir},
        )
        sys_prompt = f"{sys_prompt}\n{add_note}\n{tool_use_rule}"

        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            memory=memory,
            max_iters=max_iters,
        )
        self.max_depth = max_depth
        self.memory = memory
        self.tmp_file_storage_dir = tmp_file_storage_dir
        self.current_subtask = []

        # register all necessary tools for deep research agent
        self.toolkit.register_tool_function(view_text_file)
        self.toolkit.register_tool_function(write_text_file)
        asyncio.get_running_loop().create_task(
            self.toolkit.register_mcp_client(search_mcp_client),
        )

        self.search_function = "tavily-search"
        self.extract_function = "tavily-extract"
        self.read_file_function = "view_text_file"
        self.write_file_function = "write_text_file"
        self.summarize_function = "summarize_intermediate_results"

        self.intermediate_memory = []
        self.report_path_based = self.name + datetime.now().strftime(
            "%y%m%d%H%M%S",
        )
        self.report_index = 1
        self._required_structured_model = None
        self.user_query = None

        # add functions into toolkit
        self.toolkit.register_tool_function(self.reflect_failure)
        self.toolkit.register_tool_function(
            self.summarize_intermediate_results,
        )

    async def reply(
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """The reply method of the agent."""
        # Maintain the subtask list
        self.user_query = msg.get_text_content()
        self.current_subtask.append(
            SubTaskItem(objective=self.user_query),
        )

        # Identify the expected output and generate a plan
        await self.decompose_and_expand_subtask()
        msg.content += (
            f"\nExpected Output:\n{self.current_subtask[0].knowledge_gaps}"
        )

        # Add user query message to memory
        await self.memory.add(msg)  # type: ignore

        # Record structured output model if provided
        if structured_model:
            self._required_structured_model = structured_model
            self.toolkit.set_extended_model(
                self.finish_function_name,
                structured_model,
            )

        for _ in range(self.max_iters):
            # Generate the working plan first
            if not self.current_subtask[-1].working_plan:
                await self.decompose_and_expand_subtask()

            # Write the instruction for reasoning
            cur_plan = self.current_subtask[-1].working_plan
            cur_know_gap = self.current_subtask[-1].knowledge_gaps
            reasoning_prompt = self.prompt_dict["reasoning_prompt"].format_map(
                {
                    "objective": self.current_subtask[-1].objective,
                    "plan": cur_plan
                    if cur_plan
                    else "There is no working plan now.",
                    "knowledge_gap": f"## Knowledge Gaps:\n {cur_know_gap}"
                    if cur_know_gap
                    else "",
                    "depth": len(self.current_subtask),
                },
            )
            reasoning_prompt_msg = Msg(
                "user",
                content=[
                    TextBlock(
                        type="text",
                        text=reasoning_prompt,
                    ),
                ],
                role="user",
            )
            self.intermediate_memory.append(reasoning_prompt_msg)

            # Reasoning to generate tool calls
            backup_memory = deepcopy(self.memory)  # type: ignore
            await self.memory.add(self.intermediate_memory)  # type: ignore
            msg_reasoning = await self._reasoning()
            self.memory = backup_memory

            # Calling the tools
            for tool_call in msg_reasoning.get_content_blocks("tool_use"):
                self.intermediate_memory.append(
                    Msg(
                        self.name,
                        content=[tool_call],
                        role="assistant",
                    ),
                )  # add tool_use memory
                msg_response = await self._acting(tool_call)
                if msg_response:
                    await self.memory.add(msg_response)
                    self.current_subtask = []
                    return msg_response

        # When the maximum iterations are reached, summarize all the findings
        return await self._summarizing()

    async def _acting(self, tool_call: ToolUseBlock) -> Msg | None:
        """
        Execute a tool call and process its response with browser-specific
        handling.

        Args:
            tool_call (ToolUseBlock):
                The tool use block containing the tool name, parameters,
                and unique identifier for execution.
        Returns:
            Msg | None:
                Returns a response message if the finish function is called
                successfully, otherwise returns None to continue the
                reasoning-acting loop.
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
        update_memory = False
        intermediate_report = ""
        chunk = ""
        try:
            # Execute the tool call
            tool_res = await self.toolkit.call_tool_function(tool_call)

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
                    if len(self.current_subtask) == 0:
                        return chunk.metadata.get("response_msg")

                # Summarize intermediate results into a draft report
                elif tool_call["name"] == self.summarize_function:
                    self.intermediate_memory = []
                    await self.memory.add(
                        Msg(
                            "assistant",
                            [
                                TextBlock(
                                    type="text",
                                    text=chunk.content[0]["text"],
                                ),
                            ],
                            "assistant",
                        ),
                    )

                # Truncate the web extract results that exceeds max length
                elif tool_call["name"] in [
                    self.search_function,
                    self.extract_function,
                ]:
                    tool_res_msg.content[0]["output"] = truncate_search_result(
                        tool_res_msg.content[0]["output"],
                    )

                # Update memory when an intermediate report is generated
                if isinstance(chunk.metadata, dict) and chunk.metadata.get(
                    "update_memory",
                ):
                    update_memory = True
                    intermediate_report = chunk.metadata.get(
                        "intermediate_report",
                    )
            return None

        finally:
            # Record the tool result message in the intermediate memory
            if tool_call["name"] != self.summarize_function:
                self.intermediate_memory.append(tool_res_msg)

            # Read more information from the web page if necessary
            if tool_call["name"] == self.search_function:
                extract_res = await self._follow_up(chunk.content, tool_call)
                if isinstance(
                    extract_res.metadata,
                    dict,
                ) and extract_res.metadata.get("update_memory"):
                    self.intermediate_memory = []
                    await self.memory.add(
                        Msg(
                            "assistant",
                            content=[
                                TextBlock(
                                    type="text",
                                    text=extract_res.metadata.get(
                                        "intermediate_report",
                                    ).content[0]["text"],
                                ),
                            ],
                            role="assistant",
                        ),
                    )

            # Update memory with the intermediate report
            if update_memory:
                self.intermediate_memory = []
                await self.memory.add(
                    Msg(
                        "assistant",
                        content=[
                            TextBlock(
                                type="text",
                                text=intermediate_report.content[0]["text"],
                            ),
                        ],
                        role="assistant",
                    ),
                )

    async def get_model_output(
        self,
        msgs: list,
        format_template: Type[BaseModel] = None,
        stream: bool = True,
    ) -> Any:
        """
        Call the model and get output with or without a structured format.

        Args:
            msgs (list): A list of messages.
            format_template (BaseModel): structured format.
            stream (bool): stream-style output.
        """
        blocks = None
        if format_template:
            res = await self.model(
                await self.formatter.format(msgs=msgs),
                tools=get_dynamic_tool_call_json(
                    format_template,
                ),
            )

            if stream:
                async for content_chunk in res:
                    blocks = content_chunk.content
            else:
                blocks = res.content

            return get_structure_output(blocks)
        else:
            res = await self.model(
                await self.formatter.format(msgs=msgs),
            )

            if stream:
                async for content_chunk in res:
                    blocks = content_chunk.content
            else:
                blocks = res.content
            return blocks

    async def call_specific_tool(
        self,
        func_name: str,
        params: dict = None,
    ) -> Tuple[Msg, Msg]:
        """
        Call the specific tool in toolkit.

        Args:
            func_name (str): name of the tool.
            params (dict): input parameters of the tool.
        """
        tool_call = ToolUseBlock(
            id=shortuuid.uuid(),
            type="tool_use",
            name=func_name,
            input=params,
        )
        tool_call_msg = Msg(
            "assistant",
            [tool_call],
            role="assistant",
        )

        # get tool acting res
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
        tool_res = await self.toolkit.call_tool_function(
            tool_call,
        )
        async for chunk in tool_res:
            tool_res_msg.content[0]["output"] = chunk.content

        return tool_call_msg, tool_res_msg

    async def decompose_and_expand_subtask(self) -> ToolResponse:
        """Identify the knowledge gaps of the current subtask and generate a
        working plan by subtask decomposition. The working plan includes
        necessary steps for task completion and expanded steps.

        Returns:
            ToolResponse:
                The knowledge gaps and working plan of the current subtask
                in JSON format.
        """
        if len(self.current_subtask) <= self.max_depth:
            decompose_sys_prompt = self.prompt_dict["decompose_sys_prompt"]

            previous_plan = ""
            for i, subtask in enumerate(self.current_subtask):
                previous_plan += f"The {i}-th plan: {subtask.working_plan}\n"
            previous_plan_inst = self.prompt_dict[
                "previous_plan_inst"
            ].format_map(
                {
                    "previous_plan": previous_plan,
                    "objective": self.current_subtask[-1].objective,
                },
            )

            try:
                gaps_and_plan = await self.get_model_output(
                    msgs=[
                        Msg("system", decompose_sys_prompt, "system"),
                        Msg("user", previous_plan_inst, "user"),
                    ],
                    format_template=SubtasksDecomposition,
                    stream=self.model.stream,
                )
                response = json.dumps(
                    gaps_and_plan,
                    indent=2,
                    ensure_ascii=False,
                )
            except Exception:  # noqa: F841
                gaps_and_plan = {}
                response = self.prompt_dict["retry_hint"].format_map(
                    {"state": "decomposing the subtask"},
                )
            self.current_subtask[-1].knowledge_gaps = gaps_and_plan.get(
                "knowledge_gaps",
                None,
            )
            self.current_subtask[-1].working_plan = gaps_and_plan.get(
                "working_plan",
                None,
            )
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=response,
                    ),
                ],
            )
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=self.prompt_dict["max_depth_hint"],
                ),
            ],
        )

    async def _follow_up(
        self,
        search_results: list | str,
        tool_call: ToolUseBlock,
    ) -> ToolResponse:
        """Read the website more intensively to mine more information for
        the task. And generate a follow-up subtask if necessary to perform
        deep search.
        """

        if len(self.current_subtask) < self.max_depth:
            # Step#1: query expansion
            expansion_sys_prompt = self.prompt_dict["expansion_sys_prompt"]
            expansion_inst = self.prompt_dict["expansion_inst"].format_map(
                {
                    "objective": tool_call["input"].get("query", ""),
                    "checklist": self.current_subtask[0].knowledge_gaps,
                    "knowledge_gaps": self.current_subtask[-1].working_plan,
                    "search_results": search_results,
                },
            )

            try:
                follow_up_subtask = await self.get_model_output(
                    msgs=[
                        Msg("system", expansion_sys_prompt, "system"),
                        Msg("user", expansion_inst, "user"),
                    ],
                    format_template=WebExtraction,
                    stream=self.model.stream,
                )
            except Exception:  # noqa: F841
                follow_up_subtask = {}

            #  Step #2: extract the url
            if follow_up_subtask.get("need_more_information", False):
                expansion_response_msg = Msg(
                    "assistant",
                    follow_up_subtask.get(
                        "reasoning",
                        "I need more information.",
                    ),
                    role="assistant",
                )
                urls = follow_up_subtask.get("url", None)
                logger.info("Reading %s", urls)

                # call the extract_function
                params = {
                    "urls": urls,
                    "extract_depth": "basic",
                }
                (
                    extract_tool_use_msg,
                    extract_tool_res_msg,
                ) = await self.call_specific_tool(
                    func_name=self.extract_function,
                    params=params,
                )
                self.intermediate_memory.append(extract_tool_use_msg)

                extract_tool_res_msg.content[0][
                    "output"
                ] = truncate_search_result(
                    extract_tool_res_msg.content[0]["output"],
                )
                # await self.memory.add(tool_res_msg)
                await self.print(extract_tool_res_msg, True)
                self.intermediate_memory.append(extract_tool_res_msg)

                # Step #4: follow up judge
                try:
                    follow_up_response = await self.get_model_output(
                        msgs=[
                            Msg("user", expansion_inst, "user"),
                            expansion_response_msg,
                            extract_tool_use_msg,
                            extract_tool_res_msg,
                            Msg(
                                "user",
                                self.prompt_dict["follow_up_judge_sys_prompt"],
                                role="user",
                            ),
                        ],
                        format_template=FollowupJudge,
                        stream=self.model.stream,
                    )
                except Exception:  # noqa: F841
                    follow_up_response = {}
                if not follow_up_response.get("is_sufficient", True):
                    subtasks = follow_up_subtask.get("subtask", None)
                    logger.info("Figuring out %s", subtasks)
                    intermediate_report = (
                        await self.summarize_intermediate_results()
                    )
                    self.current_subtask.append(
                        SubTaskItem(objective=subtasks),
                    )
                    return ToolResponse(
                        content=[
                            TextBlock(
                                type="text",
                                text=follow_up_response.get(
                                    "reasoning",
                                    self.prompt_dict["need_deeper_hint"],
                                ),
                            ),
                        ],
                        metadata={
                            "update_memory": True,
                            "intermediate_report": intermediate_report,
                        },
                    )
                else:
                    return ToolResponse(
                        content=[
                            TextBlock(
                                type="text",
                                text=follow_up_response.get(
                                    "reasoning",
                                    self.prompt_dict["sufficient_hint"],
                                ),
                            ),
                        ],
                    )
            else:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=follow_up_subtask.get(
                                "reasoning",
                                self.prompt_dict["sufficient_hint"],
                            ),
                        ),
                    ],
                )
        else:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=self.prompt_dict["max_depth_hint"],
                    ),
                ],
            )

    async def summarize_intermediate_results(self) -> ToolResponse:
        """Summarize the intermediate results into a report when a step
        in working plan is completed.

        Returns:
            ToolResponse:
                The summarized draft report.
        """
        if len(self.intermediate_memory) == 0:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=self.prompt_dict["no_result_hint"],
                    ),
                ],
            )
        # agent actively call this tool
        if self.intermediate_memory[-1].name == self.summarize_function:
            blocks = await self.get_model_output(
                msgs=self.intermediate_memory
                + [
                    Msg(
                        "user",
                        self.prompt_dict["summarize_hint"].format_map(
                            {
                                "plan": self.current_subtask[-1].working_plan,
                            },
                        ),
                        role="user",
                    ),
                ],
                stream=self.model.stream,
            )
            self.current_subtask[-1].working_plan = blocks[0][
                "text"
            ]  # type: ignore[index]
        report_prefix = "#" * len(self.current_subtask)
        summarize_sys_prompt = self.prompt_dict[
            "summarize_sys_prompt"
        ].format_map(
            {"report_prefix": report_prefix},
        )
        # get all tool result
        tool_result = ""
        for item in self.intermediate_memory:
            if isinstance(item.content, str):
                tool_result += item.content + "\n"
            elif isinstance(item.content, list):
                for each in item.content:
                    if each["type"] == "tool_result":
                        tool_result += str(each) + "\n"
            else:
                logger.warning(
                    "Unknown content type: %s!",
                    type(item.content),
                )
                continue
        summarize_instruction = self.prompt_dict["summarize_inst"].format_map(
            {
                "objective": self.current_subtask[0].objective,
                "knowledge_gaps": self.current_subtask[0].knowledge_gaps,
                "working_plan": self.current_subtask[-1].working_plan,
                "tool_result": tool_result,
            },
        )

        blocks = await self.get_model_output(
            msgs=[
                Msg("system", summarize_sys_prompt, "system"),
                Msg("user", summarize_instruction, "user"),
            ],
            stream=self.model.stream,
        )
        intermediate_report = blocks[0]["text"]  # type: ignore[index]

        # Write the intermediate report
        intermediate_report_path = os.path.join(
            self.tmp_file_storage_dir,
            f"{self.report_path_based}_"
            f"inprocess_report_{self.report_index}.md",
        )
        self.report_index += 1
        params = {
            "file_path": intermediate_report_path,
            "content": intermediate_report,
        }
        await self.call_specific_tool(
            func_name=self.write_file_function,
            params=params,
        )
        logger.info(
            "Storing the intermediate findings: %s",
            intermediate_report,
        )
        if (
            self.intermediate_memory[-1].has_content_blocks("tool_use")
            and self.intermediate_memory[-1].get_content_blocks("tool_use")[0][
                "name"
            ]
            == self.summarize_function
        ):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=self.prompt_dict["update_report_hint"].format_map(
                            {
                                "intermediate_report": intermediate_report,
                                "report_path": intermediate_report_path,
                            },
                        ),
                    ),
                ],
            )
        else:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=self.prompt_dict["save_report_hint"].format_map(
                            {
                                "intermediate_report": intermediate_report,
                            },
                        ),
                    ),
                ],
            )

    async def _generate_deepresearch_report(
        self,
        checklist: str,
    ) -> Tuple[Msg, str]:
        """Collect and polish all draft reports into a final report.

        Args:
            checklist (`str`):
                The expected output items of the original task.
        """
        reporting_sys_prompt = self.prompt_dict["reporting_sys_prompt"]
        reporting_sys_prompt.format_map(
            {
                "original_task": self.user_query,
                "checklist": checklist,
            },
        )

        # Collect all intermediate reports
        if self.report_index > 1:
            inprocess_report = ""
            for index in range(self.report_index):
                params = {
                    "file_path": os.path.join(
                        self.tmp_file_storage_dir,
                        f"{self.report_path_based}_"
                        f"inprocess_report_{index + 1}.md",
                    ),
                }
                _, read_draft_tool_res_msg = await self.call_specific_tool(
                    func_name=self.read_file_function,
                    params=params,
                )
                inprocess_report += (
                    read_draft_tool_res_msg.content[0]["output"][0]["text"]
                    + "\n"
                )

            msgs = [
                Msg(
                    "system",
                    content=reporting_sys_prompt,
                    role="system",
                ),
                Msg(
                    "user",
                    content=f"Draft report:\n{inprocess_report}",
                    role="user",
                ),
            ]
        else:  # Use only intermediate memory to generate report
            msgs = [
                Msg(
                    "system",
                    content=reporting_sys_prompt,
                    role="system",
                ),
            ] + self.intermediate_memory

        blocks = await self.get_model_output(
            msgs=msgs,
            stream=self.model.stream,
        )
        final_report_content = blocks[0]["text"]  # type: ignore[index]
        logger.info(
            "The final Report is generated: %s",
            final_report_content,
        )

        # Write the final report into a file
        detailed_report_path = os.path.join(
            self.tmp_file_storage_dir,
            f"{self.report_path_based}_detailed_report.md",
        )

        params = {
            "file_path": detailed_report_path,
            "content": final_report_content,
        }
        _, write_report_tool_res_msg = await self.call_specific_tool(
            func_name=self.write_file_function,
            params=params,
        )

        return write_report_tool_res_msg, detailed_report_path

    async def _summarizing(self) -> Msg:
        """Generate a report based on the exsisting findings when the
        agent fails to solve the problem in the maximum iterations."""

        (
            summarized_content,
            _,
        ) = await self._generate_deepresearch_report(
            checklist=self.current_subtask[0].knowledge_gaps,
        )
        return Msg(
            name=self.name,
            role="assistant",
            content=json.dumps(
                summarized_content.content[0]["output"][0],
                indent=2,
                ensure_ascii=False,
            ),
        )

    async def reflect_failure(self) -> ToolResponse:
        """Reflect on the failure of the action and determine to rephrase
        the plan or deeper decompose the current step.

        Returns:
            ToolResponse:
                The reflection about plan rephrasing and subtask decomposition.
        """
        reflect_sys_prompt = self.prompt_dict["reflect_sys_prompt"]
        conversation_history = ""
        for msg in self.intermediate_memory:
            conversation_history += (
                json.dumps(
                    {"role": "user", "content": msg.content},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
        reflect_inst = self.prompt_dict["reflect_instruction"].format_map(
            {
                "conversation_history": conversation_history,
                "plan": self.current_subtask[-1].working_plan,
            },
        )
        try:
            reflection = await self.get_model_output(
                msgs=[
                    Msg("system", reflect_sys_prompt, "system"),
                    Msg("user", reflect_inst, "user"),
                ],
                format_template=ReflectFailure,
                stream=self.model.stream,
            )
            response = json.dumps(
                reflection,
                indent=2,
                ensure_ascii=False,
            )
        except Exception:  # noqa: F841
            reflection = {}
            response = self.prompt_dict["retry_hint"].format_map(
                {"state": "making the reflection"},
            )

        if reflection.get("rephrase_subtask", False) and reflection[
            "rephrase_subtask"
        ].get(
            "need_rephrase",
            False,
        ):  # type: ignore[index]
            self.current_subtask[-1].working_plan = reflection[
                "rephrase_subtask"
            ][
                "rephrased_plan"
            ]  # type: ignore[index]
        elif reflection.get("decompose_subtask", False) and reflection[
            "decompose_subtask"
        ].get(
            "need_decompose",
            False,
        ):  # type: ignore[index]
            if len(self.current_subtask) <= self.max_depth:
                intermediate_report = (
                    await self.summarize_intermediate_results()
                )
                self.current_subtask.append(
                    SubTaskItem(
                        objective=reflection[
                            "decompose_subtask"
                        ].get(  # type: ignore[index]
                            "failed_subtask",
                            None,
                        ),
                    ),
                )
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=response,
                        ),
                    ],
                    metadata={
                        "update_memory": True,
                        "intermediate_report": intermediate_report,
                    },
                )
            else:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=self.prompt_dict["max_depth_hint"],
                        ),
                    ],
                )
        else:
            pass
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=response,
                ),
            ],
        )

    # pylint: disable=invalid-overridden-method, unused-argument
    async def generate_response(  #
        self,
        response: str,
        **_kwargs: Any,
    ) -> ToolResponse:
        """Generate a detailed report as a response.

        Besides, when calling this function, the reasoning-acting memory will
        be cleared, so your response should contain a brief summary of what
        you have done so far.

        Args:
            response (`str`):
                Your response to the user.
        """
        checklist = self.current_subtask[0].knowledge_gaps
        completed_subtask = self.current_subtask.pop()

        if len(self.current_subtask) == 0:
            (
                summarized_content,
                _,
            ) = await self._generate_deepresearch_report(
                checklist=checklist,
            )
            response_msg = Msg(
                name=self.name,
                role="assistant",
                content=json.dumps(
                    summarized_content.content[0]["output"][0],
                    indent=2,
                    ensure_ascii=False,
                ),
            )
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="Successfully generated detailed report.",
                    ),
                ],
                metadata={
                    "success": True,
                    "response_msg": response_msg,
                },
                is_last=True,
            )
        else:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=self.prompt_dict[
                            "subtask_complete_hint"
                        ].format_map(
                            {
                                "cur_obj": completed_subtask.objective,
                                "next_obj": self.current_subtask[-1].objective,
                            },
                        ),
                    ),
                ],
                metadata={
                    "success": True,
                },
                is_last=True,
            )
