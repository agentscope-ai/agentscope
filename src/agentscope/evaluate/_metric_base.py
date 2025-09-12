# -*- coding: utf-8 -*-
"""The base class for _metric in evaluation."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .._utils._common import _get_timestamp
from .._utils._mixin import DictMixin
from ..types import JSONSerializableObject


@dataclass
class MetricResult(DictMixin):
    """The result of a _metric."""

    name: str
    """The metric name."""

    result: str | float | int
    """The metric result."""

    created_at: str = field(default_factory=_get_timestamp)
    """The timestamp when the metric result was created."""

    message: str | None = field(default_factory=lambda: None)
    """An optional message for the metric result, can be used to provide
    additional information or context about the result."""

    metadata: dict[str, JSONSerializableObject] | None = field(default=None)
    """Optional metadata for the metric result, can be used to store
    additional information related to the metric result."""


class MetricType(str, Enum):
    """The metric type enum."""

    CATEGORY = "category"
    """The metric result is a category, e.g. "pass" or "fail"."""

    NUMERICAL = "numerical"
    """The metric result is a numerical value, e.g. 0.95 or 100."""


class MetricBase(ABC):
    """The base class for _metric in evaluation."""

    def __init__(
        self,
        name: str,
        metric_type: MetricType,
        description: str | None = None,
        categories: list[str] | None = None,
    ) -> None:
        """Initialize the _metric object.

        Args:
            name (`str`):
                The name of the metric.
            metric_type (`MetricType`):
                The type of the metric, can be either "category" or
                "numerical", which will determine how to display the result.
            description (`str`):
                The description of the metric.
            categories (`list[str] | None`, optional):
                The candidate categories. If `metric_type` is "category", the
                categories must be provided, otherwise it should be `None`.
        """
        self.name = name
        self.metric_type = metric_type
        self.description = description

        if metric_type == MetricType.CATEGORY and categories is None:
            raise ValueError(
                "Categories must be provided for category metrics.",
            )

        self.categories = categories

    @abstractmethod
    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> MetricResult:
        """The call function to calculate the _metric result"""
