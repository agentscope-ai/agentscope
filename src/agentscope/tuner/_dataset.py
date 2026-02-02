# -*- coding: utf-8 -*-
"""DatasetConfig definition for tuner."""
from itertools import islice
from typing import List
from pydantic import BaseModel, Field


class DatasetConfig(BaseModel):
    """Dataset configuration for tuning.
    Compatible with huggingface dataset format.
    Agentscope will load the dataset from the given path using
    `datasets.load_dataset`.
    """

    path: str = Field(
        description="Path to your dataset.",
    )
    name: str | None = Field(
        description="The name of the dataset configuration.",
        default=None,
    )
    split: str | None = Field(
        description="The dataset split to use.",
        default="train",
    )
    total_epochs: int = Field(
        description="Total number of epochs to run.",
        default=1,
    )
    total_steps: int | None = Field(
        description=(
            "Total number of steps to run. "
            "If set, it will override total_epochs."
        ),
        default=None,
    )

    def preview(self, n: int = 5) -> List:
        """Preview the dataset information.

        Args:
            n (`int`): Number of samples to preview. Defaults to 5.
        """
        try:
            from datasets import load_dataset
        except ImportError as e:
            raise ImportError(
                "The `datasets` library is not installed. "
                "Please install it with `pip install datasets`.",
            ) from e
        import json

        ds = load_dataset(
            path=self.path,
            name=self.name,
            split=self.split,
            streaming=True,
        )
        samples = list(islice(ds, n))
        print(json.dumps(samples, indent=2, ensure_ascii=False))
        return samples
