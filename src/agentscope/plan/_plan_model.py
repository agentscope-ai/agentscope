# -*- coding: utf-8 -*-
"""The models used in the plan module."""
from typing import Literal

import shortuuid
from pydantic import BaseModel, Field

from .._utils._common import _get_timestamp


class SubTask(BaseModel):
    """The subtask model used in the plan module."""

    name: str = Field(
        description=(
            "The subtask name, should be concise, descriptive and not"
            "exceed 10 words."
        ),
    )
    description: str = Field(
        description=(
            "The subtask description, including the constraints, target and "
            "outcome to be achieved. The description should be clear, "
            "specific and concise, and all the constraints, target and "
            "outcome should be specific and measurable."
        ),
    )
    expected_outcome: str = Field(
        description=(
            "The expected outcome of the subtask, which should be specific, "
            "concrete and measurable."
        ),
    )
    outcome: str | None = Field(
        description="The actual outcome of the subtask.",
        exclude=True,
        default=None,
    )
    state: Literal["todo", "in_progress", "done", "abandoned"] = Field(
        description="The state of the subtask.",
        default="todo",
        exclude=True,
    )
    created_at: str = Field(
        description="The time the subtask was created.",
        default_factory=_get_timestamp,
    )
    # Result related fields
    finished_at: str | None = Field(
        description="The time the subtask was finished.",
        default=None,
        exclude=True,
    )

    def finish(self, outcome: str) -> None:
        """Finish the subtask with the actual outcome."""
        self.state = "done"
        self.outcome = outcome
        self.finished_at = _get_timestamp()

    def to_oneline_markdown(self) -> str:
        """Convert the subtask to MarkDown format."""
        status_map = {
            "todo": "- []",
            "in_progress": "- [][WIP]",
            "done": "- [x]",
            "abandoned": "- [][Abandoned]",
        }
        return f"{status_map[self.state]} {self.name}"

    def to_markdown(self, detailed: bool = False) -> str:
        """Convert the subtask to MarkDown format.

        Args:
            detailed (`bool`, defaults to `False`):
                Whether to include detailed information about the subtask.
        """
        status_map = {
            "todo": "- [ ] ",
            "in_progress": "- [ ] [WIP]",
            "done": "- [x] ",
            "abandoned": "- [ ] [Abandoned]",
        }

        if detailed:
            markdown_strs = [
                f"{status_map[self.state]}{self.name}",
                f"\t- Created At: {self.created_at}",
                f"\t- Description: {self.description}",
                f"\t- Expected Outcome: {self.expected_outcome}",
                f"\t- State: {self.state}",
            ]

            if self.state == "done":
                markdown_strs.extend(
                    [
                        f"\t- Finished At: {self.finished_at}",
                        f"\t- Actual Outcome: {self.outcome}",
                    ],
                )

            return "\n".join(markdown_strs)

        return f"{status_map[self.state]}{self.name}"


class Plan(BaseModel):
    """The plan model used in the plan module, contains a list of subtasks."""

    id: str = Field(exclude=True, default_factory=shortuuid.uuid)
    name: str = Field(
        description=(
            "The plan name, should be concise, descriptive and not exceed 10 "
            "words."
        ),
    )
    description: str = Field(
        description=(
            "The plan description, including the constraints, target and "
            "outcome to be achieved. The description should be clear, "
            "specific and concise, and all the constraints, target and "
            "outcome should be specific and measurable."
        ),
    )
    expected_outcome: str = Field(
        description=(
            "The expected outcome of the plan, which should be specific, "
            "concrete and measurable."
        ),
    )
    subtasks: list[SubTask] = Field(
        description=("A list of subtasks that make up the plan."),
    )
    created_at: str = Field(
        description="The time the plan was created.",
        default_factory=_get_timestamp,
        exclude=True,
    )
    state: Literal["todo", "in_progress", "done", "abandoned"] = Field(
        description="The state of the plan.",
        default="todo",
        exclude=True,
    )
    finished_at: str | None = Field(
        description="The time the plan was finished.",
        default=None,
        exclude=True,
    )
    outcome: str | None = Field(
        description="The actual outcome of the plan.",
        default=None,
        exclude=True,
    )

    def finish(
        self,
        state: Literal["done", "abandoned"],
        outcome: str,
    ) -> None:
        """Finish the plan."""
        self.state = state
        self.outcome = outcome
        self.finished_at = _get_timestamp()

    def to_markdown(self, detailed: bool = False) -> str:
        """Convert the plan to MarkDown format."""
        subtasks_markdown = "\n".join(
            [
                subtask.to_markdown(
                    detailed=detailed,
                )
                for subtask in self.subtasks
            ],
        )

        return "\n".join(
            [
                f"# {self.name}",
                f"**Description**: {self.description}",
                f"**Expected Outcome**: {self.expected_outcome}",
                f"**State**: {self.state}",
                f"**Created At**: {self.created_at}",
                "## Subtasks",
                subtasks_markdown,
            ],
        )
