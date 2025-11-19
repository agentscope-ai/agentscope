# -*- coding: utf-8 -*-
"""The plan notebook class, used to manage the plan, providing hints and
tool functions to the agent."""
from collections import OrderedDict
from typing import Callable, Literal, Coroutine, Any

from ._in_memory_storage import InMemoryPlanStorage
from ._plan_model import SubTask, Plan
from ._storage_base import PlanStorageBase
from .._utils._common import _execute_async_or_sync_func
from ..message import TextBlock, Msg
from ..module import StateModule
from ..tool import ToolResponse


class DefaultPlanToHint:
    """The default function to generate the hint message based on the current
    plan to guide the agent on next steps."""

    hint_prefix: str = "<system-hint>"
    hint_suffix: str = "</system-hint>"

    no_plan: str = (
        "If the user's query is complex (e.g. programming a website, game or "
        "app), or requires a long chain of steps to complete (e.g. conduct "
        "research on a certain topic from different sources), you NEED to "
        "create a plan first by calling 'create_plan'. Otherwise, you can "
        "directly execute the user's query without planning."
    )

    at_the_beginning: str = (
        "The current plan:\n"
        "```\n"
        "{plan}\n"
        "```\n"
        "Your options include:\n"
        "- Mark the first subtask as 'in_progress' by calling "
        "'update_subtask_state' with subtask_idx=0 and state='in_progress', "
        "and start executing it.\n"
        "- If the first subtask is not executable, analyze why and what you "
        "can do to advance the plan, e.g. ask user for more information, "
        "revise the plan by calling 'revise_current_plan'.\n"
        "- If the user asks you to do something unrelated to the plan, "
        "prioritize the completion of user's query first, and then return "
        "to the plan afterward.\n"
        "- If the user no longer wants to perform the current plan, confirm "
        "with the user and call the 'finish_plan' function.\n"
    )

    when_a_subtask_in_progress: str = (
        "The current plan:\n"
        "```\n"
        "{plan}\n"
        "```\n"
        "Now the subtask at index {subtask_idx}, named '{subtask_name}', is "
        "'in_progress'. Its details are as follows:\n"
        "```\n"
        "{subtask}\n"
        "```\n"
        "Your options include:\n"
        "- Go on execute the subtask and get the outcome.\n"
        "- Call 'finish_subtask' with the specific outcome if the subtask is "
        "finished.\n"
        "- Ask the user for more information if you need.\n"
        "- Revise the plan by calling 'revise_current_plan' if necessary.\n"
        "- If the user asks you to do something unrelated to the plan, "
        "prioritize the completion of user's query first, and then return to "
        "the plan afterward."
    )

    when_no_subtask_in_progress: str = (
        "The current plan:\n"
        "```\n"
        "{plan}\n"
        "```\n"
        "The first {index} subtasks are done, and there is no subtask "
        "'in_progress'. Now Your options include:\n"
        "- Mark the next subtask as 'in_progress' by calling "
        "'update_subtask_state', and start executing it.\n"
        "- Ask the user for more information if you need.\n"
        "- Revise the plan by calling 'revise_current_plan' if necessary.\n"
        "- If the user asks you to do something unrelated to the plan, "
        "prioritize the completion of user's query first, and then return to "
        "the plan afterward."
    )

    at_the_end: str = (
        "The current plan:\n"
        "```\n"
        "{plan}\n"
        "```\n"
        "All the subtasks are done. Now your options are:\n"
        "- Finish the plan by calling 'finish_plan' with the specific "
        "outcome, and summarize the whole process and outcome to the user.\n"
        "- Revise the plan by calling 'revise_current_plan' if necessary.\n"
        "- If the user asks you to do something unrelated to the plan, "
        "prioritize the completion of user's query first, and then return to "
        "the plan afterward."
    )

    def __call__(self, plan: Plan | None) -> str | None:
        """Generate the hint message based on the input plan to guide the
        agent on next steps.

        Args:
            plan (`Plan | None`):
                The current plan, used to generate the hint message.

        Returns:
            `str | None`:
                The generated hint message, or None if the plan is None or
                there is no relevant hint.
        """
        if plan is None:
            hint = self.no_plan

        else:
            # Count the number of subtasks in each state
            n_todo, n_in_progress, n_done, n_abandoned = 0, 0, 0, 0
            in_progress_subtask_idx = None
            for idx, subtask in enumerate(plan.subtasks):
                if subtask.state == "todo":
                    n_todo += 1

                elif subtask.state == "in_progress":
                    n_in_progress += 1
                    in_progress_subtask_idx = idx

                elif subtask.state == "done":
                    n_done += 1

                elif subtask.state == "abandoned":
                    n_abandoned += 1

            hint = None
            if n_in_progress == 0 and n_done == 0:
                # All subtasks are todo
                hint = self.at_the_beginning.format(
                    plan=plan.to_markdown(),
                )

            elif n_in_progress > 0 and in_progress_subtask_idx is not None:
                # One subtask is in_progress
                hint = self.when_a_subtask_in_progress.format(
                    plan=plan.to_markdown(),
                    subtask_idx=in_progress_subtask_idx,
                    subtask_name=plan.subtasks[in_progress_subtask_idx].name,
                    subtask=plan.subtasks[in_progress_subtask_idx].to_markdown(
                        detailed=True,
                    ),
                )

            elif n_in_progress == 0 and n_done > 0:
                # No subtask is in_progress, and some subtasks are done
                hint = self.when_no_subtask_in_progress.format(
                    plan=plan.to_markdown(),
                    index=n_done,
                )

            elif n_done + n_abandoned == len(plan.subtasks):
                # All subtasks are done or abandoned
                hint = self.at_the_end.format(
                    plan=plan.to_markdown(),
                )

        if hint:
            return f"{self.hint_prefix}{hint}{self.hint_suffix}"

        return hint


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
        "'finish_plan' function."
    )

    def __init__(
        self,
        max_subtasks: int | None = None,
        plan_to_hint: Callable[[Plan | None], str | None] | None = None,
        storage: PlanStorageBase | None = None,
    ) -> None:
        """Initialize the plan notebook.

        Args:
            max_subtasks (`int | None`, optional):
                The maximum number of subtasks in a plan.
            plan_to_hint (`Callable[[Plan | None], str | None] | None`, \
             optional):
                The function to generate the hint message based on the
                current plan. If not provided, a default `DefaultPlanToHint`
                object will be used.
            storage (`PlanStorageBase | None`, optional):
                The plan storage. If not provided, an in-memory storage will
                be used.
        """
        super().__init__()

        self.max_tasks = max_subtasks
        self.plan_to_hint = plan_to_hint or DefaultPlanToHint()
        self.storage = storage or InMemoryPlanStorage()

        self.current_plan: Plan | None = None

        self._plan_change_hooks = OrderedDict()

        # Register the current_plan state for state management
        self.register_state(
            "current_plan",
            custom_to_json=lambda _: _.model_dump() if _ else None,
            custom_from_json=lambda _: Plan.model_validate(_) if _ else None,
        )

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

        if self.current_plan is None:
            res = ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Plan '{name}' created successfully.",
                    ),
                ],
            )

        else:
            res = ToolResponse(
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

        self.current_plan = plan
        await self._trigger_plan_change_hooks()
        return res

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
        # Validate the arguments first
        response: list[str] = []
        if isinstance(subtask_idx, str):
            try:
                subtask_idx = int(subtask_idx)
            except ValueError:
                pass

        if not isinstance(subtask_idx, int):
            response.append(
                f"Invalid type for argument 'subtask_idx'. "
                f"Expected 'int', but got '{type(subtask_idx)}'.",
            )

        if action not in ["add", "revise", "delete"]:
            response.append(
                f"Invalid action '{action}'. Must be one of 'add', 'revise', "
                f"'delete'.",
            )

        if action in ["add", "revise"] and subtask is None:
            response.append(
                f"The subtask must be provided when action is '{action}', "
                "but got None.",
            )

        self._validate_current_plan()

        # validate subtask_idx
        if action != "add" and subtask_idx >= len(self.current_plan.subtasks):
            response.append(
                f"Invalid subtask_idx '{subtask_idx}' for action '{action}'. "
                f"Must be between 0 "
                f"and {len(self.current_plan.subtasks) - 1}.",
            )

        if action == "add" and not (
            0 <= subtask_idx <= len(self.current_plan.subtasks)
        ):
            response.append(
                f"Invalid subtask_idx '{subtask_idx}' for action 'add'. "
                f"Must be between 0 and {len(self.current_plan.subtasks)}.",
            )

        if response:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="Error: " + response[0],
                    ),
                ],
            )

        if subtask is not None:
            # Convert to SubTask model if it's a dict
            subtask = SubTask.model_validate(subtask)

        if action == "delete":
            subtask = self.current_plan.subtasks.pop(subtask_idx)
            await self._trigger_plan_change_hooks()
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Subtask (named '{subtask.name}') at index "
                        f"{subtask_idx} is deleted successfully.",
                    ),
                ],
            )

        if action == "add" and subtask:
            self.current_plan.subtasks.insert(subtask_idx, subtask)
            await self._trigger_plan_change_hooks()
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"New subtask is added successfully at index "
                        f"{subtask_idx}.",
                    ),
                ],
            )

        # revise
        self.current_plan.subtasks[subtask_idx] = subtask
        await self._trigger_plan_change_hooks()
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
        state: Literal["todo", "in_progress", "abandoned"],
    ) -> ToolResponse:
        """Update the state of a subtask by given index and state. Note if you
        want to mark a subtask as done, you SHOULD call `finish_subtask`
        instead with the specific outcome.

        Args:
            subtask_idx (`int`):
                The index of the subtask to be updated, starting from 0.
            state (`Literal["todo", "in_progress", "abandoned"]`):
                The new state of the subtask. If you want to mark a subtask
                as done, you SHOULD call `finish_subtask` instead with the
                specific outcome.
        """
        self._validate_current_plan()

        if isinstance(subtask_idx, str):
            try:
                subtask_idx = int(subtask_idx)
            except ValueError:
                pass

        if not isinstance(subtask_idx, int):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Invalid type for argument 'subtask_idx'. "
                        f"Expected 'int', but got '{type(subtask_idx)}'.",
                    ),
                ],
            )

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

        if state not in ["todo", "in_progress", "abandoned"]:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Invalid state '{state}'. Must be one of "
                        "'todo', 'in_progress', 'abandoned'.",
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
                    "abandoned",
                ]:
                    return ToolResponse(
                        content=[
                            TextBlock(
                                type="text",
                                text=(
                                    f"Subtask (at index {idx}) named "
                                    f"'{subtask.name}' is not done yet. You "
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
                                    f"'{subtask.name}' is already "
                                    "'in_progress'. You should finish it "
                                    "first before starting another subtask."
                                ),
                            ),
                        ],
                    )

        self.current_plan.subtasks[subtask_idx].state = state

        # Update the plan state to in_progress if not already
        suffix = self.current_plan.refresh_plan_state()

        await self._trigger_plan_change_hooks()
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Subtask at index {subtask_idx}, named "
                    f"'{self.current_plan.subtasks[subtask_idx].name}' "
                    f"is marked as '{state}' successfully. " + suffix,
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
                expected outcome in the sub-task description. SHOULDN't be
                what you did or general description, e.g. "I have searched
                xxx", "I have written the code for xxx", etc. It SHOULD be
                the specific data, information, or path to the file, e.g.
                "There are 5 articles about xxx, they are\n- xxx\n- xxx\n..."
        """
        self._validate_current_plan()

        if isinstance(subtask_idx, str):
            try:
                subtask_idx = int(subtask_idx)
            except ValueError:
                pass

        if not isinstance(subtask_idx, int):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Invalid type for argument 'subtask_idx'. "
                        f"Expected 'int', but got '{type(subtask_idx)}'.",
                    ),
                ],
            )

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
            if subtask.state not in ["done", "abandoned"]:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=(
                                "Cannot finish subtask at index "
                                f"{subtask_idx} because the previous "
                                f"subtask (at index {idx}) named "
                                f"'{subtask.name}' is not done yet. You "
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
            await self._trigger_plan_change_hooks()
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"Subtask (at index {subtask_idx}) named "
                            f"'{self.current_plan.subtasks[subtask_idx].name}'"
                            " is marked as done successfully. The next "
                            f"subtask named '{next_subtask.name}' is "
                            f"activated."
                        ),
                    ),
                ],
            )

        await self._trigger_plan_change_hooks()
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

    async def finish_plan(
        self,
        state: Literal["done", "abandoned"],
        outcome: str,
    ) -> ToolResponse:
        """Finish the current plan by given outcome, or abandon it with the
        given reason if the user no longer wants to perform it. Note that you
        SHOULD confirm with the user before abandoning the plan.

        Args:
            state (`Literal["done", "abandoned"]`):
                The state to finish the plan. If "done", the plan will be
                marked as done with the given outcome. If "abandoned", the
                plan will be abandoned with the given reason.
            outcome (`str`):
                The specific outcome of the plan if state is "done", or the
                reason for abandoning the plan if state is "abandoned".
        """
        if self.current_plan is None:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="There is no plan to finish.",
                    ),
                ],
            )

        self.current_plan.finish(state, outcome)

        # Store the finished plan into history
        await self.storage.add_plan(self.current_plan)

        self.current_plan = None
        await self._trigger_plan_change_hooks()
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"The current plan is finished successfully as "
                    f"'{state}'.",
                ),
            ],
        )

    async def view_historical_plans(self) -> ToolResponse:
        """View the historical plans."""
        historical_plans = await self.storage.get_plans()

        plans_str = [
            f"""Plan named '{_.name}':
- ID: {_.id}
- Created at: {_.created_at}
- Description: {_.description}
- State: {_.state}
"""
            for _ in historical_plans
        ]

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="\n".join(plans_str),
                ),
            ],
        )

    async def recover_historical_plan(self, plan_id: str) -> ToolResponse:
        """Recover a historical plan by given plan ID, the plan ID can be
        obtained by calling `view_historical_plans`. Note the recover
        operation will override the current plan if exists.

        Args:
            plan_id (`str`):
                The ID of the historical plan to be recovered.
        """
        historical_plan = await self.storage.get_plan(plan_id)
        if historical_plan is None:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Cannot find the plan with ID '{plan_id}'.",
                    ),
                ],
            )

        # Store the current plan into history if exists
        if self.current_plan:
            if self.current_plan.state != "done":
                self.current_plan.finish(
                    "abandoned",
                    f"The plan execution is interrupted by a new plan "
                    f"with ID '{historical_plan.id}'.",
                )
            await self.storage.add_plan(self.current_plan)
            res = ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            "The current plan named "
                            f"'{self.current_plan.name}' is replaced by the "
                            f"historical plan named '{historical_plan.name}' "
                            f"with ID '{historical_plan.id}'."
                        ),
                    ),
                ],
            )
        else:
            res = ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"Historical plan named '{historical_plan.name}' "
                            f"with ID '{historical_plan.id}' is recovered "
                            "successfully."
                        ),
                    ),
                ],
            )
        self.current_plan = historical_plan
        return res

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
            # subtask related tools
            self.view_subtasks,
            self.update_subtask_state,
            self.finish_subtask,
            # plan related tools
            self.create_plan,
            self.revise_current_plan,
            self.finish_plan,
            # historical plan related tools
            self.view_historical_plans,
            self.recover_historical_plan,
        ]

    async def get_current_hint(self) -> Msg | None:
        """Get the hint message based on the current plan and subtasks states.
        This function will call the `plan_to_hint` function to generate the
        hint message.

        Returns:
            `Msg | None`:
                The hint message wrapped by <system-hint></system-hint>, or
                None if there is no relevant hint.
        """
        hint_content = self.plan_to_hint(self.current_plan)
        if hint_content:
            msg = Msg(
                "user",
                hint_content,
                "user",
            )
            return msg

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

    async def _trigger_plan_change_hooks(self) -> None:
        """Trigger all the plan change hooks."""
        for hook in self._plan_change_hooks.values():
            await _execute_async_or_sync_func(
                hook,
                self,
                self.current_plan,
            )
