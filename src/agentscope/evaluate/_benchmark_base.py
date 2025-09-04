# -*- coding: utf-8 -*-
"""The base class for benchmark evaluation."""
from abc import ABC, abstractmethod
from typing import Generator

from ._task import Task


class BenchmarkBase(ABC):
    """The base class for benchmark evaluation."""

    name: str
    """The name of the benchmark."""

    description: str
    """The description of the benchmark."""

    def __init__(self, name: str, description: str) -> None:
        """Initialize the benchmark.

        Args:
            name (`str`):
                The name of the benchmark.
            description (`str`):
                A brief description of the benchmark.
        """
        self.name = name
        self.description = description

    @abstractmethod
    def __iter__(self) -> Generator[Task, None, None]:
        """Iterate over the benchmark."""
        raise NotImplementedError("Subclasses must implement this method.")

    @abstractmethod
    def __len__(self) -> int:
        """Get the length of the benchmark."""
        raise NotImplementedError("Subclasses must implement this method.")

    @abstractmethod
    def __getitem__(self, index: int) -> Task:
        """Get the task at the given index."""
        raise NotImplementedError("Subclasses must implement this method.")
