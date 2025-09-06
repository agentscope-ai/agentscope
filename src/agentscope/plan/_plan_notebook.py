# -*- coding: utf-8 -*-
"""The plan notebook class, used to manage the plan, providing hints and
tool functions to the agent."""
from collections import OrderedDict
from typing import Callable, Literal, Coroutine, Any

from pydantic import BaseModel

from ._plan_model import SubTask, Plan
from ._storage_base import PlanStorageBase
from ..message import TextBlock, Msg
from ..module import StateModule
from ..tool import ToolResponse


class ReasoningHints(BaseModel):
    """The hints to be inserted before the agent's reasoning process to
    guide the agent on next steps."""

    hint_prefix: str = "<system-hint>"
    hint_suffix: str = "</system-hint>"

    at_the_beginning: str = (
        "Currently, you have a plan as follows:\n"
        "```\n"
        "{plan}\n"
        "```\n"
        "Your options include\n"
        "- mark the first subtask as 'in_progress' by calling "
        "update_subtask_state with subtask_idx=0 and state='in_progress', "
        "and start executing it.\n"
        "- If the first subtask cannot be executed directly, you can ask the "
        "user for more information or revise the plan by calling "
        "'revise_current_plan'.\n"
    )

    when_a_subtask_in_progress: str = (
        "Currently, you have a plan as follows:\n"
        "```\n"
        "{plan}\n"
        "```\n"
        "Now the subtask at index {subtask_idx}, named {subtask_name}, is "
        "'in_progress'. Its details are as follows:\n"
        "```\n"
        "{subtask}\n"
        "```\n"
        "Your options include:\n"
        "- execute the subtask and get the outcome.\n"
        "- if you finish it, call 'finish_subtask' with the specific "
        "outcome.\n"
        "- ask the user for more information if you need.\n"
        "- revise the plan by calling 'revise_current_plan' if necessary.\n"
    )

    when_no_subtask_in_progress: str = (
        "Currently, you have a plan as follows:\n"
        "```\n"
        "{plan}\n"
        "```\n"
        "The first {index} subtasks are done, and there is no subtask "
        "'in_progress'. Now Your options are:\n"
        "- update the next subtask as 'in_progress' by calling "
        "'update_subtask_state', and start executing it.\n"
        "- ask the user for more information if you need.\n"
        "- revise the plan by calling 'revise_current_plan' if necessary.\n"
    )

    at_the_end: str = (
        "Currently, you have a plan as follows:\n"
        "```\n"
        "{plan}\n"
        "```\n"
        "All the subtasks are done. Now your options are:\n"
        "- finish the plan by calling 'finish_plan' with the specific "
        "outcome, and summarize the whole process and outcome to the user.\n"
        "- revise the plan by calling 'revise_current_plan' if necessary.\n"
    )


class PlanNotebook(StateModule):
    """The plan notebook to manage the plan, providing hints and plan related
    tool functions to the agent."""

    _plan_change_hooks: dict[str, Callable[["PlanNotebook", Plan], None]]
    """The hooks that will be triggered when the plan is changed. For example,
    used to display the plan on the frontend."""

    description: str = (
        "The plan-related tools. Activate this tool when you need to execute "
        "complex task, e.g. building a website or a game. Once activated, "
        "you'll enter the plan mode, where you will be guided to complete "
        "the given query by creating and following a plan, and hint message "
        "wrapped by <system-hint></system-hint> will guide you to complete "
        "the task. If you think the user no longer wants to perform the "
        "current task, you need to confirm with the user and call the "
        "`finish_plan` function."
    )

    def __init__(
        self,
        max_tasks: int | None = None,
        max_iters_per_task: int = 50,
        hints: ReasoningHints | None = None,
        storage: PlanStorageBase | None = None,
    ) -> None:
        """Initialize the plan notebook.

        Args:
            max_tasks (`int | None`, optional):
                The maximum number of subtasks in a plan.
            max_iters_per_task (`int`, defaults to 50):
                The maximum number of iterations for each subtask. If
                exceeded, the sub-task will be marked as failed.
            hints (`ReasoningHints | None`, optional):
                The hints to guide the agent before reasoning. If not provided,
                a default hint will be used.
            storage (`PlanStorageBase | None`, optional):
                The plan storage. If not provided, an in-memory storage will
                be used.
        """
        super().__init__()

        self.max_tasks = max_tasks
        self.max_iters_per_task = max_iters_per_task
        self.hints = hints or ReasoningHints()
        self.storage = storage or PlanStorageBase()

        self.current_plan: Plan | None = None

        self._plan_change_hooks = OrderedDict()

    async def create_plan(
        self,
        name: str,
        description: str,
        expected_outcome: str,
        subtasks: list[SubTask],
    ) -> ToolResponse:
        """Create a plan by given name and sub-tasks.

        Args:
            name (`str`):
                The plan name, should be concise, descriptive and not exceed
                10 words.
            description (`str`):
                The plan description, including the constraints, target and
                outcome to be achieved. The description should be clear,
                specific and concise, and all the constraints, target and
                outcome should be specific and measurable.
            expected_outcome (`str`):
                The expected outcome of the plan, which should be specific,
                concrete and measurable.
            subtasks (`list[SubTask]`):
                A list of sequential sub-tasks that make up the plan.

        Returns:
            `ToolResponse`:
                The response of the tool call.
        """
        plan = Plan(
            name=name,
            description=description,
            expected_outcome=expected_outcome,
            subtasks=subtasks,
        )
        await self.storage.add_plan(plan)

        if self.current_plan is None:
            self.current_plan = plan
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Plan '{name}' created successfully.",
                    ),
                ],
            )
        else:
            self.current_plan = plan
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            "The current plan named "
                            f"'{self.current_plan.name}' is replaced by the "
                            f"newly created plan named '{name}'."
                        ),
                    ),
                ],
            )

    def _validate_current_plan(self) -> None:
        """Validate the current plan."""
        if self.current_plan is None:
            raise ValueError(
                "The current plan is None, you need to create a plan by "
                "calling create_plan() first.",
            )

    async def revise_current_plan(
        self,
        subtask_idx: int,
        action: Literal["add", "revise", "delete"],
        subtask: SubTask | None = None,
    ) -> ToolResponse:
        """Revise the current plan by adding, revising or deleting a sub-task.

        Args:
            subtask_idx (`int`):
                The index of the sub-task to be revised, starting from 0.
            action (`Literal["add", "revise", "delete"]`):
                The action to be performed on the sub-task. If "add", the
                sub-task will be inserted before the given index. If "revise",
                the sub-task at the given index will be revised. If "delete",
                the sub-task at the given index will be deleted.
            subtask (`SubTask | None`, optional):
                The sub-task to be added or revised. Required if action is
                "add" or "revise".

        Raises:
            `ValueError`:
                If the current plan is `None`, `ValueError` will be raised.

        Returns:
            `ToolResponse`:
                The response of the tool call.
        """
        if action not in ["add", "revise", "delete"]:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Invalid action '{action}'. Must be one of "
                        "'add', 'revise', 'delete'.",
                    ),
                ],
            )

        if action in ["add", "revise"] and subtask is None:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"The subtask must be provided when action is "
                        f"'{action}', but got None.",
                    ),
                ],
            )

        self._validate_current_plan()

        # validate subtask_idx
        if subtask_idx >= len(self.current_plan.subtasks):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Invalid subtask_idx '{subtask_idx}'. Must "
                        f"be between 0 and "
                        f"{len(self.current_plan.subtasks) - 1}.",
                    ),
                ],
            )

        if action == "delete":
            subtask = self.current_plan.subtasks.pop(subtask_idx)
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Subtask (named {subtask.name}) at index "
                        f"{subtask_idx} is deleted successfully.",
                    ),
                ],
            )

        if action == "add" and subtask:
            self.current_plan.subtasks.insert(subtask_idx, subtask)
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"New subtask is added successfully at index "
                        f"{subtask_idx}.",
                    ),
                ],
            )

        self.current_plan.subtasks[subtask_idx] = subtask
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Subtask at index {subtask_idx} is revised "
                    f"successfully.",
                ),
            ],
        )

    async def update_subtask_state(
        self,
        subtask_idx: int,
        state: Literal["todo", "in_progress", "deprecated"],
    ) -> ToolResponse:
        """Update the state of a subtask by given index and state. Note if you
        want to mark a subtask as done, you SHOULD call `finish_subtask`
        instead with the specific outcome.

        Args:
            subtask_idx (`int`):
                The index of the subtask to be updated, starting from 0.
            state (`Literal["todo", "in_progress", "deprecated"]`):
                The new state of the subtask. If you want to mark a subtask
                as done, you SHOULD call `finish_subtask` instead with the
                specific outcome.
        """
        self._validate_current_plan()

        if not 0 <= subtask_idx < len(self.current_plan.subtasks):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Invalid subtask_idx '{subtask_idx}'. Must "
                        f"be between 0 and "
                        f"{len(self.current_plan.subtasks) - 1}.",
                    ),
                ],
            )

        if state not in ["todo", "in_progress", "deprecated"]:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Invalid state '{state}'. Must be one of "
                        "'todo', 'in_progress', 'deprecated'.",
                    ),
                ],
            )

        # Only one subtask can be in_progress at a time
        if state == "in_progress":
            # Check only one subtask is in_progress
            for idx, subtask in enumerate(self.current_plan.subtasks):
                # Check all previous subtasks are done or deprecated
                if idx < subtask_idx and subtask.state not in [
                    "done",
                    "deprecated",
                ]:
                    return ToolResponse(
                        content=[
                            TextBlock(
                                type="text",
                                text=(
                                    f"Subtask (at index {idx}) named "
                                    f"{subtask.name} is not done yet. You "
                                    "should finish the previous subtasks "
                                    "first."
                                ),
                            ),
                        ],
                    )

                # Check no other subtask is in_progress
                if subtask.state == "in_progress":
                    return ToolResponse(
                        content=[
                            TextBlock(
                                type="text",
                                text=(
                                    f"Subtask (at index {idx}) named "
                                    f"{subtask.name} is already "
                                    "'in_progress'. You should finish it "
                                    "first before starting another subtask."
                                ),
                            ),
                        ],
                    )

        self.current_plan.subtasks[subtask_idx].state = state
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Subtask at index {subtask_idx}, named "
                    f"'{self.current_plan.subtasks[subtask_idx].name}' "
                    f"is marked as '{state}' successfully.",
                ),
            ],
        )

    async def finish_subtask(
        self,
        subtask_idx: int,
        subtask_outcome: str,
    ) -> ToolResponse:
        """Label the subtask as done by given index and outcome.

        Args:
            subtask_idx (`int`):
                The index of the sub-task to be marked as done, starting
                from 0.
            subtask_outcome (`str`):
                The specific outcome of the sub-task, should exactly match the
                expected outcome in the sub-task description.
        """
        self._validate_current_plan()

        if not 0 <= subtask_idx < len(self.current_plan.subtasks):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Invalid subtask_idx '{subtask_idx}'. Must "
                        f"be between 0 and "
                        f"{len(self.current_plan.subtasks) - 1}.",
                    ),
                ],
            )

        for idx, subtask in enumerate(
            self.current_plan.subtasks[0:subtask_idx],
        ):
            if subtask.state not in ["done", "deprecated"]:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=(
                                "Cannot finish subtask at index "
                                f"{subtask_idx} because the previous "
                                f"subtask (at index {idx}) named "
                                f"{subtask.name} is not done yet. You "
                                "should finish the previous subtasks first."
                            ),
                        ),
                    ],
                )

        # Label the subtask as done
        self.current_plan.subtasks[subtask_idx].finish(subtask_outcome)
        # Auto activate the next subtask if exists
        if subtask_idx + 1 < len(self.current_plan.subtasks):
            self.current_plan.subtasks[subtask_idx + 1].state = "in_progress"
            next_subtask = self.current_plan.subtasks[subtask_idx + 1]
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"Subtask (at index {subtask_idx}) named "
                            f"'{self.current_plan.subtasks[subtask_idx].name}'"
                            " is marked as done successfully. The next "
                            f"subtask named {next_subtask.name} is activated."
                        ),
                    ),
                ],
            )

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"Subtask (at index {subtask_idx}) named "
                        f"'{self.current_plan.subtasks[subtask_idx].name}'"
                        " is marked as done successfully. "
                    ),
                ),
            ],
        )

    async def view_subtasks(self, subtask_idx: list[int]) -> ToolResponse:
        """View the details of the sub-tasks by given indexes.

        Args:
            subtask_idx (`list[int]`):
                The indexes of the sub-tasks to be viewed, starting from 0.
        """
        self._validate_current_plan()

        gathered_strs = []
        invalid_subtask_idx = []
        for idx in subtask_idx:
            if not 0 <= idx < len(self.current_plan.subtasks):
                invalid_subtask_idx.append(idx)
                continue

            subtask_markdown = self.current_plan.subtasks[idx].to_markdown(
                detailed=True,
            )
            gathered_strs.append(
                f"Subtask at index {idx}:\n"
                "```\n"
                f"{subtask_markdown}\n"
                "```\n",
            )

        if invalid_subtask_idx:
            gathered_strs.append(
                f"Invalid subtask_idx '{invalid_subtask_idx}'. Must be "
                f"between 0 and {len(self.current_plan.subtasks) - 1}.",
            )

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="\n".join(gathered_strs),
                ),
            ],
        )

    def list_tools(
        self,
    ) -> list[Callable[..., Coroutine[Any, Any, ToolResponse]]]:
        """List all tool functions provided to agent

        Returns:
            `list[Callable[..., ToolResponse]]`:
                A list of all tool functions provided by the plan notebook to
                the agent.
        """
        return [
            self.create_plan,
            self.view_subtasks,
            self.revise_current_plan,
            self.update_subtask_state,
            self.finish_subtask,
        ]

    def get_current_hint(self) -> Msg | None:
        """Get the hint message based on the current plan and subtasks
        states.

        - If all subtasks are "todo", return the 'at_the_beginning' hint of
        the ReasoningHints.
        - If one subtask is "in_progress", return the
        'when_a_subtask_in_progress' hint of the ReasoningHints.
        - If no subtask is "in_progress", and some subtasks are "done",
        return the 'when_no_subtask_in_progress' hint of the ReasoningHints.
        - If all subtasks are "done", return the 'at_the_end' hint of the
        ReasoningHints.
        """
        if self.current_plan is None:
            return None

        n_todo, n_in_progress, n_done, n_deprecated = 0, 0, 0, 0

        in_progress_subtask_idx = None
        for idx, subtask in enumerate(self.current_plan.subtasks):
            match subtask.state:
                case "todo":
                    n_todo += 1
                case "in_progress":
                    n_in_progress += 1
                    in_progress_subtask_idx = idx
                case "done":
                    n_done += 1
                case "deprecated":
                    n_deprecated += 1
                case _:
                    raise ValueError(
                        f"Invalid subtask state '{subtask.state}'.",
                    )

        content = None
        if n_todo == len(self.current_plan.subtasks):
            content = self.hints.at_the_beginning.format(
                plan=self.current_plan.to_markdown(),
            )

        elif in_progress_subtask_idx:
            subtask = self.current_plan.subtasks[in_progress_subtask_idx]
            content = self.hints.when_a_subtask_in_progress.format(
                plan=self.current_plan.to_markdown(),
                subtask_idx=in_progress_subtask_idx,
                subtask_name=subtask.name,
                subtask=subtask.to_markdown(detailed=True),
            )

        elif n_in_progress == 0:
            content = self.hints.when_no_subtask_in_progress.format(
                plan=self.current_plan.to_markdown(),
                index=n_done,
            )

        elif n_done == len(self.current_plan.subtasks):
            content = self.hints.at_the_end.format(
                plan=self.current_plan.to_markdown(),
            )

        if content:
            return Msg(
                "user",
                content,
                "user",
            )
        return None

    def register_plan_change_hook(
        self,
        hook_name: str,
        hook: Callable[["PlanNotebook", Plan], None],
    ) -> None:
        """Register a plan hook that will be triggered when the plan is
        changed.

        Args:
            hook_name (`str`):
                The name of the hook, should be unique.
            hook (`Callable[[Plan], None]`):
                The hook function, which takes the current plan as input and
                returns nothing.
        """
        self._plan_change_hooks[hook_name] = hook

    def remove_plan_change_hook(self, hook_name: str) -> None:
        """Remove a plan change hook by given name.

        Args:
            hook_name (`str`):
                The name of the hook to be removed.
        """
        if hook_name in self._plan_change_hooks:
            self._plan_change_hooks.pop(hook_name)
        else:
            raise ValueError(f"Hook '{hook_name}' not found.")
