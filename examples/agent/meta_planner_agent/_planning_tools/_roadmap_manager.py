# -*- coding: utf-8 -*-
"""
Planning handler module for meta planner
"""
from typing import Optional, Literal

from agentscope.module import StateModule
from agentscope.tool import ToolResponse
from agentscope.message import TextBlock

from ._planning_notebook import (
    PlannerNoteBook,
    SubTaskStatus,
    Update,
    SubTaskSpecification,
)


class RoadmapManager(StateModule):
    """Handles planning operations for meta planner agent.

    This class provides functionality for task decomposition, roadmap creation,
    and roadmap revision.
    """

    def __init__(
        self,
        planner_notebook: PlannerNoteBook,
    ):
        """Initialize the PlanningHandler.

        Args:
            planner_notebook (PlannerNoteBook):
                Data structure containing planning state.
        """
        super().__init__()
        self.planner_notebook = planner_notebook

    async def decompose_task_and_build_roadmap(
        self,
        user_latest_input: str,
        given_task_conclusion: str,
        detail_analysis_for_plan: str,
        decomposed_subtasks: list[SubTaskSpecification],
    ) -> ToolResponse:
        """
        Analysis the user subtask, generate a comprehensive reasoning for how
        to decompose the task into multiple subtasks.

        Args:
            user_latest_input (str):
                The latest user input. If there are multiple rounds
                of user input, faithfully record the latest user input.
            given_task_conclusion (str):
                The user's task to decompose. If there are multiple rounds
                of user input, analysis and give the key idea of the task that
                the user really you to solve.
            detail_analysis_for_plan (str):
                A detailed analysis of how a task should be decomposed.
            decomposed_subtasks (list[SubTaskSpecification]):
                List of subtasks that was decomposed.
        """
        self.planner_notebook.detail_analysis_for_plan = (
            detail_analysis_for_plan
        )
        self.planner_notebook.roadmap.original_task = given_task_conclusion
        for subtask in decomposed_subtasks:
            if isinstance(subtask, dict):
                subtask_status = SubTaskStatus(
                    subtask_specification=SubTaskSpecification(
                        **subtask,
                    ),
                )
            elif isinstance(subtask, SubTaskSpecification):
                subtask_status = SubTaskStatus(
                    subtask_specification=subtask,
                )
            else:
                raise TypeError(
                    "Unexpected type of `decomposed_subtasks`,"
                    "which is expected to strictly follow List of "
                    "SubTaskSpecification.",
                )
            self.planner_notebook.roadmap.decomposed_tasks.append(
                subtask_status,
            )
        self.planner_notebook.user_input.append(user_latest_input)
        return ToolResponse(
            metadata={"success": True},
            content=[
                TextBlock(
                    type="text",
                    text="Successfully decomposed the task into subtasks",
                ),
            ],
        )

    async def get_next_unfinished_subtask_from_roadmap(self) -> ToolResponse:
        """
        Obtains the next unfinished subtask from the roadmap.
        """
        idx, subtask = self.planner_notebook.roadmap.next_unfinished_subtask()
        if idx is None or subtask is None:
            return ToolResponse(
                metadata={"success": False},
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            "No unfinished subtask was found. "
                            "Either all subtasks have been done, or the task"
                            " has not been decomposed."
                        ),
                    ),
                ],
            )
        return ToolResponse(
            metadata={"success": True, "subtask": subtask},
            content=[
                TextBlock(
                    type="text",
                    text=f"Next unfinished subtask idx: {idx}",
                ),
                TextBlock(
                    type="text",
                    text=subtask.model_dump_json(indent=2),
                ),
            ],
        )

    async def revise_roadmap(
        self,
        action: Literal["add_subtask", "revise_subtask", "remove_subtask"],
        subtask_idx: int,
        subtask_specification: Optional[SubTaskSpecification] = None,
        update_to_subtask: Optional[Update] = None,
        new_status: Literal["Planned", "In-process", "Done"] = "In-process",
    ) -> ToolResponse:
        """After subtasks are done by worker agents, use this function to
        revise the progress and details of the current roadmap.

        Updates the status of subtasks and potentially revises input/output
        descriptions and required tools for tasks based on current progress
        and available information.

        Args:
            action (
                `Literal["add_subtask", "revise_subtask", "remove_subtask"]`
            ):
                Action to perform on the roadmap.
            subtask_idx (`int`):
                Index of the subtask to revise its status. This index starts
                with 0.
            subtask_specification (`SubTaskSpecification`):
                Revised subtask specification. When you use `add_subtask` or
                `revise_subtask` action, you MUST provide this field with
                revised `exact_input` and `expected_output` according to
                the execution context.
            update_to_subtask (`Update`):
                Generate an update record for this subtask based on the
                worker execution report. When you use `revise_subtask` action,
                you MUST provide this field.
            new_status  (`Literal["Planned", "In-process", "Done"]`):
                The new status of the subtask.

        Returns:
            ToolResponse:
                Response indicating success/failure of the revision
                and any updates made. May request additional human
                input if needed.
        """
        num_subtasks = len(self.planner_notebook.roadmap.decomposed_tasks)
        if isinstance(subtask_specification, dict):
            subtask_specification = SubTaskSpecification(
                **subtask_specification,
            )
        elif subtask_specification is None and action in [
            "add_subtask",
            "revise_subtask",
        ]:
            return ToolResponse(
                metadata={"success": False},
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"Choosing {action} must have valid "
                            f"`subtask_specification` field."
                        ),
                    ),
                ],
            )

        if isinstance(update_to_subtask, dict):
            update_to_subtask = Update(
                **update_to_subtask,
            )
        elif update_to_subtask is None and action == "revise_subtask":
            return ToolResponse(
                metadata={"success": False},
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"Choosing {action} must have valid "
                            f"`update_to_subtask` field."
                        ),
                    ),
                ],
            )

        if subtask_idx >= num_subtasks and action == "add_subtask":
            self.planner_notebook.roadmap.decomposed_tasks.append(
                SubTaskStatus(
                    subtask_specification=subtask_specification,
                    status="Planned",
                    updates=update_to_subtask,
                ),
            )
            return ToolResponse(
                metadata={"success": True},
                content=[
                    TextBlock(
                        type="text",
                        text=f"add new subtask with index {subtask_idx}.",
                    ),
                ],
            )
        elif subtask_idx >= num_subtasks:
            return ToolResponse(
                metadata={"success": False},
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"Fail to update subtask {subtask_idx} status."
                            f"There are {num_subtasks} subtasks, "
                            f"idx {subtask_idx} is not supported with "
                            f"action {action}."
                        ),
                    ),
                ],
            )
        elif action == "revise_subtask" and update_to_subtask:
            subtask = self.planner_notebook.roadmap.decomposed_tasks[
                subtask_idx
            ]
            subtask.status = new_status
            subtask.updates.append(update_to_subtask)
            return ToolResponse(
                metadata={"success": True},
                content=[
                    TextBlock(
                        type="text",
                        text=f"Update subtask {subtask_idx} status.",
                    ),
                    TextBlock(
                        type="text",
                        text=self.planner_notebook.roadmap.decomposed_tasks[
                            subtask_idx
                        ].model_dump_json(indent=2),
                    ),
                ],
            )
        elif action == "remove_subtask":
            self.planner_notebook.roadmap.decomposed_tasks.pop(subtask_idx)
            return ToolResponse(
                metadata={"success": True},
                content=[
                    TextBlock(
                        type="text",
                        text=f"Remove subtask {subtask_idx} from roadmap.",
                    ),
                ],
            )
        else:
            raise ValueError(
                f"Not support action {action} on subtask {subtask_idx}",
            )
