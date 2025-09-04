# -*- coding: utf-8 -*-
"""
Meta Planner agent class that can handle complicated tasks with
planning-execution pattern.
"""
import os
import uuid
from typing import Optional, Any, Literal
import json
from datetime import datetime
from pathlib import Path

from agentscope.message import Msg, ToolUseBlock, TextBlock, ToolResultBlock
from agentscope.tool import Toolkit, ToolResponse
from agentscope.model import ChatModelBase
from agentscope.formatter import FormatterBase
from agentscope.memory import MemoryBase
from agentscope.agent import ReActAgent

from _planning_tools import (  # pylint: disable=C0411
    PlannerNoteBook,
    RoadmapManager,
    WorkerManager,
    share_tools,
)

PlannerStage = Literal["post_reasoning", "post_action", "pre_reasoning"]


def _infer_planner_stage_with_msg(
    cur_msg: Msg,
) -> tuple[PlannerStage, list[str]]:
    """
    Infer the planner stage and extract tool names from a message.

    Analyzes a message to determine the current stage of the planner workflow
    and extracts any tool names if tool calls are present in the message.

    Args:
        cur_msg (Msg): The message to analyze for stage inference.

    Returns:
        tuple[PlannerStage, list[str]]: A tuple containing:
            - PlannerStage: One of "pre_reasoning", "post_reasoning", or
                "post_action"
            - list[str]: List of tool names found in tool_use or
                tool_result blocks

    Note:
        - "pre_reasoning": System role messages with string content
        - "post_reasoning": Messages with tool_use blocks or plain text content
        - "post_action": Messages with tool_result blocks
        - Tool names are extracted from both tool_use and tool_result blocks
    """
    blocks = cur_msg.content
    if isinstance(blocks, str) and cur_msg.role in ["system", "user"]:
        return "pre_reasoning", []

    cur_tool_names = [
        str(b.get("name", "no_name_tool"))
        for b in blocks
        if b["type"] in ["tool_use", "tool_result"]
    ]
    if cur_msg.has_content_blocks("tool_result"):
        return "post_action", cur_tool_names
    elif cur_msg.has_content_blocks("tool_use"):
        return "post_reasoning", cur_tool_names
    else:
        return "post_reasoning", cur_tool_names


def update_user_input_pre_reply_hook(
    self: "MetaPlanner",
    kwargs: dict[str, Any],
) -> None:
    """Hook for loading user input to planner notebook"""
    msg = kwargs.get("msg", None)
    if isinstance(msg, Msg):
        msg = [msg]
    if isinstance(msg, list):
        for m in msg:
            self.planner_notebook.user_input.append(m.content)


def planner_save_post_reasoning_state(
    self: "MetaPlanner",
    reasoning_input: dict[str, Any],  # pylint: disable=W0613
    reasoning_output: Msg,
) -> None:
    """Hook func for save state after reasoning step"""
    if self.state_saving_dir:
        os.makedirs(self.state_saving_dir, exist_ok=True)
        cur_stage, _ = _infer_planner_stage_with_msg(reasoning_output)
        time_str = datetime.now().strftime("%Y%m%d%H%M%S")
        file_path = os.path.join(
            self.state_saving_dir,
            f"state-{cur_stage}-{time_str}.json",
        )
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.state_dict(), f, ensure_ascii=False, indent=4)


async def planner_load_state_pre_reasoning_hook(
    self: "MetaPlanner",  # pylint: disable=W0613
    *args: Any,
    **kwargs: Any,
) -> None:
    """Hook func for loading saved state after reasoning step"""
    mem_msgs = await self.memory.get_memory()
    if len(mem_msgs) > 0:
        stage, _ = _infer_planner_stage_with_msg(mem_msgs[-1])
        if stage == "post_reasoning":
            self.state_loading_reasoning_msg = mem_msgs[-1]
            # delete the last reasoning message to avoid error when
            # calling model in reasoning step
            await self.memory.delete(len(mem_msgs) - 1)


async def planner_load_state_post_reasoning_hook(
    self: "MetaPlanner",  # pylint: disable=W0613
    *args: Any,
    **kwargs: Any,
) -> Msg:
    """Hook func for loading saved state after reasoning step"""
    if self.state_loading_reasoning_msg is not None:
        num_msgs = await self.memory.size()
        # replace the newly generated reasoning message with the loaded one
        await self.memory.delete(num_msgs - 1)
        old_reasoning_msg = self.state_loading_reasoning_msg
        await self.memory.add(old_reasoning_msg)
        self.state_loading_reasoning_msg = None
        return old_reasoning_msg


async def planner_compose_reasoning_msg_pre_reasoning_hook(
    self: "MetaPlanner",  # pylint: disable=W0613
    *args: Any,
    **kwargs: Any,
) -> None:
    """Hook func for composing msg for reasoning step"""
    reasoning_info = (
        "## All User Input\n{all_user_input}\n\n"
        "## Session Context\n"
        "```json\n{notebook_string}\n```\n\n"
    ).format_map(
        {
            "notebook_string": self.planner_notebook.model_dump_json(
                exclude={"user_input", "full_tool_list"},
                indent=2,
            ),
            "all_user_input": self.planner_notebook.user_input,
        },
    )
    reasoning_msg = Msg(
        "user",
        content=reasoning_info,
        role="user",
    )
    await self.memory.add(reasoning_msg)


async def planner_remove_reasoning_msg_post_reasoning_hook(
    self: "MetaPlanner",  # pylint: disable=W0613
    *args: Any,
    **kwargs: Any,
) -> None:
    """Hook func for removing msg for reasoning step"""
    num_msgs = await self.memory.size()
    if num_msgs > 1:
        # remove the msg added by planner_compose_reasoning_pre_reasoning_hook
        await self.memory.delete(num_msgs - 2)


def planner_save_post_action_state(
    self: "MetaPlanner",
    action_input: dict[str, Any],
    tool_output: Optional[Msg],  # pylint: disable=W0613
) -> None:
    """Hook func for save state after action step"""
    if self.state_saving_dir:
        os.makedirs(self.state_saving_dir, exist_ok=True)
        time_str = datetime.now().strftime("%Y%m%d%H%M%S")
        file_path = os.path.join(
            self.state_saving_dir,
            "state-post-action-"
            f"{action_input.get('tool_call').get('name')}-{time_str}.json",
        )
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.state_dict(), f, ensure_ascii=False, indent=4)


class MetaPlanner(ReActAgent):
    """
    A meta-planning agent that extends ReActAgent with enhanced planning
    capabilities. The MetaPlanner is designed to handle complex multistep
    planning tasks by leveraging a combination of reasoning and action
    capabilities. The subtasks will be solved by dynamically create ReAct
    worker agent and provide it with necessary tools.
    """

    def __init__(
        self,
        name: str,
        model: ChatModelBase,
        worker_full_toolkit: Toolkit,
        formatter: FormatterBase,
        memory: MemoryBase,
        toolkit: Toolkit,
        agent_working_dir: str,
        sys_prompt: Optional[str] = None,
        max_iters: int = 10,
        state_saving_dir: Optional[str] = None,
        planner_mode: Literal["disable", "dynamic", "enforced"] = "dynamic",
    ) -> None:
        """
        Initialize the MetaPlanner with the given parameters.

        Args:
            name (str):
                The name identifier for this agent instance.
            model (ChatModelBase):
                The primary chat model used for reasoning and response
                generation.
            worker_full_toolkit (Toolkit):
                Complete set of tools available to the worker agent.
            formatter (FormatterBase):
                Formatter for formatting messages to the model API provider's
                format.
            memory (MemoryBase):
                Memory system for storing conversation history and context.
            toolkit (Toolkit):
                Toolkit for managing tools available to the agent.
            agent_working_dir (str):
                Directory for agent's file operations.
            sys_prompt (str, optional):
                Meta planner's system prompt
            max_iters (int, optional):
                Maximum number of planning iterations. Defaults to 10.
            state_saving_dir (Optional[str], optional):
                Directory to save the agent's state. Defaults to None.
            planner_mode (bool, optional):
                Enable planner mode for solving tasks. Defaults to True.
        """
        name = "Task-Meta-Planner" if name is None else name
        if sys_prompt is None:
            sys_prompt = (
                "You are a helpful assistant named Task-Meta-Planner."
                "If a given task can not be done easily, then you may need "
                "to use the tool `enter_solving_complicated_task_mode` to "
                "change yourself to a more long-term planning mode."
            )

        # Call super().__init__() early to initialize StateModule attributes
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            memory=memory,
            toolkit=toolkit,
            max_iters=max_iters,
        )

        self.agent_working_dir_root = agent_working_dir
        self.task_dir = self.agent_working_dir_root
        self.worker_full_toolkit = worker_full_toolkit
        self.state_saving_dir = state_saving_dir

        # if we load a trajectory and the last step was reasoning,
        # then we need a buffer to store the reasoning message and replace
        # with this message after reasoning
        self.state_loading_reasoning_msg: Optional[Msg] = None

        # for debugging and state resume, we need a flag to indicate
        self.planner_mode = planner_mode
        self.in_planner_mode = False
        self.register_state("planner_mode")
        self.register_state("in_planner_mode")

        self.planner_notebook = None
        self.roadmap_manager, self.worker_manager = None, None
        if planner_mode in ["dynamic", "enforced"]:
            self.planner_notebook = PlannerNoteBook()
            self.prepare_planner_tools(planner_mode)
            self.register_state(
                "planner_notebook",
                lambda x: x.model_dump(),
                lambda x: PlannerNoteBook(**x),
            )

        # pre-reply hook
        self.register_instance_hook(
            "pre_reply",
            "update_user_input_to_notebook_pre_reply_hook",
            update_user_input_pre_reply_hook,
        )
        # pre-reasoning hook
        self.register_instance_hook(
            "pre_reasoning",
            "planner_load_state_pre_reasoning_hook",
            planner_load_state_pre_reasoning_hook,
        )
        self.register_instance_hook(
            "pre_reasoning",
            "planner_compose_reasoning_msg_pre_reasoning_hook",
            planner_compose_reasoning_msg_pre_reasoning_hook,
        )
        # post_reasoning hook
        self.register_instance_hook(
            "post_reasoning",
            "planner_load_state_post_reasoning_hook",
            planner_load_state_post_reasoning_hook,
        )
        self.register_instance_hook(
            "post_reasoning",
            "planner_remove_reasoning_msg_post_reasoning_hook",
            planner_remove_reasoning_msg_post_reasoning_hook,
        )
        self.register_instance_hook(
            "post_reasoning",
            "save_state_post_reasoning_hook",
            planner_save_post_reasoning_state,
        )
        # post_action_hook
        self.register_instance_hook(
            "post_acting",
            "save_state_post_action_hook",
            planner_save_post_action_state,
        )

    def prepare_planner_tools(
        self,
        planner_mode: Literal["disable", "enforced", "dynamic"],
    ) -> None:
        """
        Prepare tool to planning depending on the selected mode.
        """
        self.roadmap_manager = RoadmapManager(
            planner_notebook=self.planner_notebook,
        )

        self.worker_manager = WorkerManager(
            worker_model=self.model,
            worker_formatter=self.formatter,
            planner_notebook=self.planner_notebook,
            agent_working_dir=self.task_dir,
            worker_full_toolkit=self.worker_full_toolkit,
        )
        # clean
        self.toolkit.remove_tool_groups("planning")
        self.toolkit.create_tool_group(
            "planning",
            "Tool group for planning capability",
        )
        # re-register planning tool to enable loading the correct info
        self.toolkit.register_tool_function(
            self.roadmap_manager.decompose_task_and_build_roadmap,
            group_name="planning",
        )
        self.toolkit.register_tool_function(
            self.roadmap_manager.revise_roadmap,
            group_name="planning",
        )
        self.toolkit.register_tool_function(
            self.roadmap_manager.get_next_unfinished_subtask_from_roadmap,
            group_name="planning",
        )
        self.toolkit.register_tool_function(
            self.worker_manager.show_current_worker_pool,
            group_name="planning",
        )
        self.toolkit.register_tool_function(
            self.worker_manager.create_worker,
            group_name="planning",
        )
        self.toolkit.register_tool_function(
            self.worker_manager.execute_worker,
            group_name="planning",
        )

        if planner_mode == "dynamic":
            if "enter_solving_complicated_task_mode" not in self.toolkit.tools:
                self.toolkit.register_tool_function(
                    self.enter_solving_complicated_task_mode,
                )
            # Only activate after agent decides to enter the
            # planning-execution mode
            self.toolkit.update_tool_groups(["planning"], False)
        elif planner_mode == "enforced":
            self.toolkit.update_tool_groups(["planning"], True)
            # use the self.agent_working_dir as working dir
            self._update_toolkit_and_sys_prompt()

    def _ensure_file_system_functions(self) -> None:
        required_tool_list = [
            "read_file",
            "write_file",
            "edit_file",
            "create_directory",
            "list_directory",
            "directory_tree",
            "list_allowed_directories",
        ]
        for tool_name in required_tool_list:
            if tool_name not in self.worker_full_toolkit.tools:
                raise ValueError(
                    f"{tool_name} must be in the worker toolkit and "
                    "its tool group must be active for complicated.",
                )
        share_tools(self.worker_full_toolkit, self.toolkit, required_tool_list)

    async def enter_solving_complicated_task_mode(
        self,
        task_name: str,
    ) -> ToolResponse:
        """
        When the user task meets any of the following conditions, enter the
        solving complicated task mode by using this tool.
        1. the task cannot be done within 5 reasoning-acting iterations;
        2. the task cannot be done by the current tools you can see;
        3. the task is related to comprehensive research or information
            gathering

        Args:
            task_name (`str`):
                Given a name to the current task as an indicator. Because
                this name will be used to create a directory, so try to
                use "_" instead of space between words, e.g. "A_NEW_TASK".
        """
        # build directory for the task
        self._ensure_file_system_functions()
        self.task_dir = os.path.join(
            self.agent_working_dir_root,
            task_name,
        )
        self.worker_manager.agent_working_dir = self.task_dir

        create_task_dir = ToolUseBlock(
            type="tool_use",
            id=str(uuid.uuid4()),
            name="create_directory",
            input={
                "path": self.task_dir,
            },
        )
        tool_res = await self.toolkit.call_tool_function(create_task_dir)
        tool_res_msg = Msg(
            "system",
            content=[
                ToolResultBlock(
                    type="tool_result",
                    output=[],
                    name="create_directory",
                    id=create_task_dir["id"],
                ),
            ],
            role="system",
        )
        async for chunk in tool_res:
            # Turn into a tool result block
            tool_res_msg.content[0]["output"] = chunk.content
        await self.print(tool_res_msg)

        self._update_toolkit_and_sys_prompt()
        return ToolResponse(
            metadata={"success": True},
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "Successfully enter the planning-execution mode to "
                        "solve complicated task. "
                        "All the file operations, including"
                        "read/write/modification, should be done in directory "
                        f"{self.task_dir}"
                    ),
                ),
            ],
        )

    def _update_toolkit_and_sys_prompt(self) -> None:
        # change agent settings for solving complicated task
        full_worker_tool_list = [
            {
                "tool_name": func_dict.get("function", {}).get("name", ""),
                "description": func_dict.get("function", {}).get(
                    "description",
                    "",
                ),
            }
            for func_dict in self.worker_full_toolkit.get_json_schemas()
        ]
        self.planner_notebook.full_tool_list = full_worker_tool_list
        with open(
            Path(__file__).parent
            / "_built_in_long_sys_prompt"
            / "meta_planner_sys_prompt.md",
            "r",
            encoding="utf-8",
        ) as f:
            sys_prompt = f.read()
        sys_prompt = sys_prompt.format_map(
            {
                "tool_list": json.dumps(
                    full_worker_tool_list,
                    ensure_ascii=False,
                ),
            },
        )
        self._sys_prompt = sys_prompt  # pylint: disable=W0201
        self.toolkit.update_tool_groups(["planning"], True)
        self.in_planner_mode = True

    def resume_planner_tools(self) -> None:
        """Resume the planner notebook for tools"""
        self.prepare_planner_tools(self.planner_mode)
        if self.in_planner_mode:
            self._update_toolkit_and_sys_prompt()
