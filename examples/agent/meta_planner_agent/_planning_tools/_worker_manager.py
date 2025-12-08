# -*- coding: utf-8 -*-
"""
Coordination handler module for meta planner
"""
import os
from pathlib import Path
import json
from typing import Optional, Literal, List
from agentscope import logger

from agentscope.module import StateModule
from agentscope.tool import Toolkit
from agentscope.memory import InMemoryMemory, MemoryBase
from agentscope.tool import ToolResponse
from agentscope.message import Msg, TextBlock, ToolUseBlock, ToolResultBlock
from agentscope.model import ChatModelBase, DashScopeChatModel
from agentscope.formatter import FormatterBase, DashScopeChatFormatter
from agentscope.agent import ReActAgent

from ._planning_notebook import (
    WorkerInfo,
    WorkerResponse,
)
from ._planning_notebook import (
    PlannerNoteBook,
)


def rebuild_reactworker(
    worker_info: WorkerInfo,
    old_toolkit: Toolkit,
    new_toolkit: Toolkit,
    memory: Optional[MemoryBase] = None,
    model: Optional[ChatModelBase] = None,
    formatter: Optional[FormatterBase] = None,
    exclude_tools: Optional[list[str]] = None,
) -> ReActAgent:
    """
    Rebuild a ReActAgent worker with specified configuration and tools.

    Creates a new ReActAgent using worker information and toolkit
    configuration. Tools are shared from the old toolkit to the new one,
    excluding any specified tools.

    Args:
        worker_info (WorkerInfo): Information about the worker including name,
            system prompt, and tool lists.
        old_toolkit (Toolkit): Source toolkit containing available tools.
        new_toolkit (Toolkit): Destination toolkit to receive shared tools.
        memory (Optional[MemoryBase], optional): Memory instance for the agent.
            Defaults to InMemoryMemory() if None.
        model (Optional[ChatModelBase], optional): Chat model instance.
            Defaults to DashscopeChatModel with deepseek-r1 if None.
        formatter (Optional[FormatterBase], optional): Message formatter.
            Defaults to DashScopeChatFormatter() if None.
        exclude_tools (Optional[list[str]], optional): List of tool names to
            exclude from sharing. Defaults to empty list if None.

    Returns:
        ReActAgent: A configured ReActAgent instance ready for use.

    Note:
        - The default model uses the DASHSCOPE_API_KEY environment variable
        - Tools are shared based on worker_info.tool_lists minus excluded tools
        - The agent is configured with thinking enabled and streaming support
    """
    if exclude_tools is None:
        exclude_tools = []
    tool_list = [
        tool_name
        for tool_name in worker_info.tool_lists
        if tool_name not in exclude_tools
    ]
    share_tools(old_toolkit, new_toolkit, tool_list)
    model = (
        model
        if model
        else DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="deepseek-r1",
            enable_thinking=True,
            stream=True,
        )
    )
    return ReActAgent(
        name=worker_info.worker_name,
        sys_prompt=worker_info.sys_prompt,
        model=model,
        formatter=formatter if formatter else DashScopeChatFormatter(),
        toolkit=new_toolkit,
        memory=InMemoryMemory() if memory is None else memory,
        max_iters=20,  # hardcoded the max iteration for now
    )


async def check_file_existence(file_path: str, toolkit: Toolkit) -> bool:
    """
    Check if a file exists using the read_file tool from the provided toolkit.

    This function attempts to verify file existence by calling the read_file
    tool and checking the response for error indicators. It requires the
    toolkit to have a 'read_file' tool available.

    Args:
        file_path (str): The path to the file to check for existence.
        toolkit (Toolkit): The toolkit containing the read_file tool.

    Returns:
        bool: True if the file exists and is readable, False otherwise.

    Note:
        - Returns False if the 'read_file' tool is not available in the toolkit
        - Returns False if any exception occurs during the file read attempt
        - Uses error message detection ("no such file or directory") to
            determine existence
    """
    if "read_file" in toolkit.tools:
        params = {
            "path": file_path,
        }
        read_file_block = ToolUseBlock(
            type="tool_use",
            id="manual_check_file_existence",
            name="read_file",
            input=params,
        )
        try:
            tool_res = await toolkit.call_tool_function(read_file_block)
            tool_res_msg = Msg(
                "system",
                [
                    ToolResultBlock(
                        type="tool_result",
                        id="",
                        name="read_file",
                        output=[],
                    ),
                ],
                "system",
            )
            async for chunk in tool_res:
                # Turn into a tool result block
                tool_res_msg.content[0][  # type: ignore[index]
                    "output"
                ] = chunk.content
            if "no such file or directory" in str(tool_res_msg.content):
                return False
            else:
                return True
        except Exception as _:  # noqa: F841
            return False

    else:
        return False


def share_tools(
    old_toolkit: Toolkit,
    new_toolkit: Toolkit,
    tool_list: list[str],
) -> None:
    """
    Share specified tools from an old toolkit to a new toolkit.

    This function copies tools from one toolkit to another based on the
    provided tool list. If a tool doesn't exist in the old toolkit,
    a warning is logged.

    Args:
        old_toolkit (Toolkit):
            The source toolkit containing tools to be shared.
        new_toolkit (Toolkit):
            The destination toolkit to receive the tools.
        tool_list (list[str]):
            List of tool names to be copied from old to new toolkit.

    Returns:
        None

    Note:
        This function modifies the new_toolkit in place.
        If a tool in tool_list is not found in old_toolkit,
        a warning is logged but execution continues.
    """
    for tool in tool_list:
        if tool in old_toolkit.tools and tool not in new_toolkit.tools:
            new_toolkit.tools[tool] = old_toolkit.tools[tool]
        else:
            logger.warning(
                "No tool %s in the provided worker_tool_toolkit",
                tool,
            )


class WorkerManager(StateModule):
    """
    Handles coordination between meta planner and worker agents.

    This class manages the creation, selection, and execution of worker agents
    to accomplish subtasks in a roadmap. It provides functionality for dynamic
    worker creation, worker selection based on task requirements, and
    processing worker responses to update the overall task progress.
    """

    def __init__(
        self,
        worker_model: ChatModelBase,
        worker_formatter: FormatterBase,
        planner_notebook: PlannerNoteBook,
        worker_full_toolkit: Toolkit,
        agent_working_dir: str,
        worker_pool: Optional[dict[str, tuple[WorkerInfo, ReActAgent]]] = None,
    ):
        """Initialize the CoordinationHandler.
        Args:
            worker_model (ChatModelBase):
                Main language model for coordination decisions
            worker_formatter (FormatterBase):
                Message formatter for model communication
            planner_notebook (PlannerNoteBook):
                Notebook containing roadmap and file information
            worker_full_toolkit (Toolkit):
                Complete toolkit available to workers
            agent_working_dir (str):
                Working directory for the agent operations
            worker_pool: dict[str, tuple[WorkerInfo, ReActAgent]]:
                workers that has already been created
        """
        super().__init__()
        self.planner_notebook = planner_notebook
        self.worker_model = worker_model
        self.worker_formatter = worker_formatter
        self.worker_pool: dict[str, tuple[WorkerInfo, ReActAgent]] = (
            worker_pool if worker_pool else {}
        )
        self.agent_working_dir = agent_working_dir
        self.worker_full_toolkit = worker_full_toolkit

        def reconstruct_workerpool(worker_pool_dict: dict) -> dict:
            rebuild_worker_pool = {}
            for k, v in worker_pool_dict.items():
                worker_info = WorkerInfo(**v)
                rebuild_worker_pool[k] = (
                    worker_info,
                    rebuild_reactworker(
                        worker_info=worker_info,
                        old_toolkit=self.worker_full_toolkit,
                        new_toolkit=Toolkit(),
                        model=self.worker_model,
                        formatter=self.worker_formatter,
                        exclude_tools=["generate_response"],
                    ),
                )
            return rebuild_worker_pool

        self.register_state(
            "worker_pool",
            lambda x: {k: v[0].model_dump() for k, v in x.items()},
            custom_from_json=reconstruct_workerpool,
        )

    def _register_worker(
        self,
        agent: ReActAgent,
        description: Optional[str] = None,
        worker_type: Literal["built-in", "dynamic-built"] = "dynamic",
    ) -> None:
        """
        Register a worker agent in the worker pool.

        Adds a worker agent to the available pool with appropriate metadata.
        Handles name conflicts by appending version numbers when necessary.

        Args:
            agent (ReActAgent):
                The worker agent to register
            description (Optional[str]):
                Description of the worker's capabilities
            worker_type (Literal["built-in", "dynamic-built"]):
                Type of worker agent
        """
        worker_info = WorkerInfo(
            worker_name=agent.name,
            description=description,
            worker_type=worker_type,
            status="ready-to-work",
        )
        if worker_type == "dynamic-built":
            worker_info.sys_prompt = agent.sys_prompt
            worker_info.tool_lists = list(agent.toolkit.tools.keys())

        if agent.name in self.worker_pool:
            name = agent.name
            version = 1
            while name in self.worker_pool:
                name = agent.name + f"_v{version}"
                version += 1
            agent.name, worker_info.worker_name = name, name
            self.worker_pool[name] = (worker_info, agent)
        else:
            self.worker_pool[agent.name] = (worker_info, agent)

    @staticmethod
    def _no_more_subtask_return() -> ToolResponse:
        """
        Return response when no more unfinished subtasks exist.

        Returns:
            ToolResponse: Response indicating no more subtasks are available
        """
        return ToolResponse(
            metadata={"success": False},
            content=[
                TextBlock(
                    type="text",
                    text="No more subtask exists. "
                    "Check whether the task is "
                    "completed solved.",
                ),
            ],
        )

    async def create_worker(
        self,
        worker_name: str,
        worker_system_prompt: str,
        tool_names: Optional[List[str]] = None,
        agent_description: str = "",
    ) -> ToolResponse:
        """
        Create a worker agent for the next unfinished subtask.

        Dynamically creates a specialized worker agent based on the
        requirements of the next unfinished subtask in the roadmap.
        The worker is configured with appropriate tools and system prompts
        based on the task needs.

        Args:
            worker_name (str): The name of the worker agent.
            worker_system_prompt (str): The system prompt for the worker agent.
            tool_names (Optional[List[str]], optional):
                List of tools that should be assigned to the worker agent so
                that it can finish the subtask. MUST be from the
                `Available Tools for workers`
            agent_description (str, optional):
                A brief description of the worker's capabilities.

        Returns:
            ToolResponse: Response containing the creation result and worker
                details
        """
        if tool_names is None:
            tool_names = []
        elif isinstance(tool_names, str):
            try:
                tool_names = json.loads(tool_names)
            except json.JSONDecodeError:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=f"Invalid arguments: the argument"
                            f" {tool_names} should be a list, but got"
                            f" {type(tool_names)}.",
                        ),
                    ],
                )
        worker_toolkit = Toolkit()
        share_tools(
            self.worker_full_toolkit,
            worker_toolkit,
            tool_names
            + [
                "read_file",
                "write_file",
                "edit_file",
                "search_files",
                "list_directory",
            ],
        )
        with open(
            Path(__file__).parent.parent
            / "_built_in_long_sys_prompt"
            / "_worker_additional_sys_prompt.md",
            "r",
            encoding="utf-8",
        ) as f:
            additional_worker_prompt = f.read()
        with open(
            Path(__file__).parent.parent
            / "_built_in_long_sys_prompt"
            / "_tool_usage_rules.md",
            "r",
            encoding="utf-8",
        ) as f:
            additional_worker_prompt += str(f.read()).format_map(
                {"agent_working_dir": self.agent_working_dir},
            )
        worker = ReActAgent(
            name=worker_name,
            sys_prompt=(worker_system_prompt + additional_worker_prompt),
            model=self.worker_model,
            formatter=self.worker_formatter,
            memory=InMemoryMemory(),
            toolkit=worker_toolkit,
        )

        self._register_worker(
            worker,
            description=agent_description,
            worker_type="dynamic-built",
        )

        return ToolResponse(
            metadata={"success": True},
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"Successfully created a worker agent:\n"
                        f"Worker name: {worker_name}"
                        f"Worker tools: {tool_names}"
                        f"Worker system prompt: {worker.sys_prompt}"
                    ),
                ),
            ],
        )

    async def show_current_worker_pool(self) -> ToolResponse:
        """
        List all currently available worker agents with
        their system prompts and tools.
        """
        worker_info: dict[str, dict] = {
            name: info.model_dump()
            for name, (info, _) in self.worker_pool.items()
        }
        return ToolResponse(
            metadata={"success": True},
            content=[
                TextBlock(
                    type="text",
                    text=json.dumps(worker_info, ensure_ascii=False, indent=2),
                ),
            ],
        )

    async def execute_worker(
        self,
        subtask_idx: int,
        selected_worker_name: str,
        detailed_instruction: str,
    ) -> ToolResponse:
        """
        Execute a worker agent for the next unfinished subtask.

        Args:
            subtask_idx (int):
                Index of the subtask to execute.
            selected_worker_name (str):
                Select a worker agent to execute by its name. If you are unsure
                what are the available agents, call `show_current_worker_pool`
                before using this function.
            detailed_instruction (str):
                Generate detailed instruction for the worker based on the
                next unfinished subtask in the roadmap. If you are unsure
                what is the next unavailable subtask, check with
                `get_next_unfinished_subtask_from_roadmap` to get more info.
        """
        if selected_worker_name not in self.worker_pool:
            worker_info: dict[str, WorkerInfo] = {
                name: info for name, (info, _) in self.worker_pool.items()
            }
            current_agent_pool = json.dumps(
                worker_info,
                ensure_ascii=False,
                indent=2,
            )
            return ToolResponse(
                metadata={"success": False},
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"There is no {selected_worker_name} in current"
                            "agent pool."
                            "Current agent pool:\n```json"
                            f"{current_agent_pool}\n"
                            "```"
                        ),
                    ),
                ],
            )

        worker = self.worker_pool[selected_worker_name][1]
        question_msg = Msg(
            role="user",
            name="user",
            content=detailed_instruction,
        )
        worker_response_msg = await worker(
            question_msg,
            structured_model=WorkerResponse,
        )
        if worker_response_msg.metadata is not None:
            worker_response = WorkerResponse(
                **worker_response_msg.metadata,
            )
            self.planner_notebook.roadmap.decomposed_tasks[
                subtask_idx
            ].workers.append(
                self.worker_pool[selected_worker_name][0],
            )
            # double-check to ensure the generated files exists
            for filepath, desc in worker_response.generated_files.items():
                if await check_file_existence(
                    filepath,
                    self.worker_full_toolkit,
                ):
                    self.planner_notebook.files[filepath] = desc
                else:
                    worker_response.generated_files.pop(filepath)

            return ToolResponse(
                metadata={
                    "success": True,
                    "worker_response": worker_response.model_dump_json(),
                },
                content=[
                    TextBlock(
                        type="text",
                        text=worker_response.model_dump_json(),
                    ),
                ],
            )
        else:
            return ToolResponse(
                metadata={
                    "success": False,
                    "worker_response": worker_response_msg.content,
                },
                content=[
                    TextBlock(
                        type="text",
                        text=str(worker_response_msg.content),
                    ),
                ],
            )
