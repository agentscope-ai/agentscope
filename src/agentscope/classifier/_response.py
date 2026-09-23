# -*- coding: utf-8 -*-
"""Provider-independent response types for classifier models."""
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from ._usage import ClassifierUsage
from .._utils._common import _generate_id, _generate_timestamp
from .._utils._mixin import DictMixin
from ..types import JSONSerializableObject


@dataclass
class BinaryAnswer(DictMixin):
    """The probability of a positive answer to a binary question."""

    probability: float
    """The positive-outcome probability, between zero and one."""

    type: Literal["binary_answer"] = field(
        default_factory=lambda: "binary_answer",
    )
    """The answer type discriminator."""


@dataclass
class ChoiceAnswer(DictMixin):
    """The selected alternative and its probability distribution."""

    choice: str
    """The alternative with the highest probability."""

    confidence: float
    """The provider-reported confidence in the selected alternative."""

    probabilities: dict[str, float]
    """The probability of each requested alternative."""

    type: Literal["choice_answer"] = field(
        default_factory=lambda: "choice_answer",
    )
    """The answer type discriminator."""


@dataclass
class ScoreAnswer(DictMixin):
    """An expected score and its probability distribution."""

    score: float
    """The probability-weighted expected score."""

    confidence: float
    """The provider-reported confidence in the score."""

    legend: dict[int, JSONSerializableObject]
    """The requested descriptions keyed by integer score level.

    JSON serialization converts these integer keys to strings.
    """

    probabilities: dict[int, float]
    """The probability of each integer score level.

    JSON serialization converts these integer keys to strings.
    """

    type: Literal["score_answer"] = field(
        default_factory=lambda: "score_answer",
    )
    """The answer type discriminator."""


#: A provider-independent classifier answer.
ClassifierAnswer: TypeAlias = BinaryAnswer | ChoiceAnswer | ScoreAnswer


@dataclass
class ClassifierResponse(DictMixin):
    """The normalized response of a classifier model."""

    model: str
    """The concrete model that produced the answers."""

    content: dict[str, ClassifierAnswer]
    """Answers keyed by the corresponding question names."""

    usage: ClassifierUsage | None = field(default_factory=lambda: None)
    """Usage information reported for this invocation."""

    id: str = field(default_factory=_generate_id)
    """The unique response identifier."""

    created_at: str = field(default_factory=_generate_timestamp)
    """When the response was created."""

    type: Literal["classifier_response"] = field(
        default_factory=lambda: "classifier_response",
    )
    """The response type discriminator."""

    metadata: dict[str, JSONSerializableObject] = field(
        default_factory=dict,
    )
    """Additional provider-independent response metadata."""
