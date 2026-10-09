# -*- coding: utf-8 -*-
"""Provider-independent question types for classifier models."""
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


class BinaryCriteria(BaseModel):
    """Optional descriptions for the two outcomes of a binary question."""

    model_config = ConfigDict(extra="forbid")

    true: str | None = None
    """What counts as a positive outcome."""

    false: str | None = None
    """What counts as a negative outcome."""


class BinaryQuestion(BaseModel):
    """A question whose answer is a probability between zero and one."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["binary"] = "binary"
    """The question type discriminator."""

    instructions: str | None = None
    """The question or statement to evaluate."""

    criteria: BinaryCriteria | None = None
    """Optional descriptions of the positive and negative outcomes."""


class ChoiceQuestion(BaseModel):
    """A question that selects one named alternative."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["choice"] = "choice"
    """The question type discriminator."""

    criteria: dict[str, str | None] = Field(min_length=1)
    """Named alternatives and optional descriptions."""

    instructions: str | None = None
    """The decision the classifier should make."""


class ScoreQuestion(BaseModel):
    """A question that assigns an expected score using an ordered rubric."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["score"] = "score"
    """The question type discriminator."""

    criteria: list[str] = Field(min_length=1)
    """Ordered score descriptions, starting at score zero."""

    instructions: str | None = None
    """The value the classifier should score."""


#: A provider-independent classifier question.
ClassifierQuestion: TypeAlias = Annotated[
    BinaryQuestion | ChoiceQuestion | ScoreQuestion,
    Field(discriminator="type"),
]
