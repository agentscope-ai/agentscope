# -*- coding: utf-8 -*-
"""The GAIA benchmark class in agentscope."""
import json
import os
from typing import Generator
from pathlib import Path

from ...evaluate._gaia_benchmark._gaia_metric import GAIAAccuracy
from .._benchmark_base import BenchmarkBase
from .._task import Task


class GAIABenchmark(BenchmarkBase):
    """The GAIA benchmark for evaluating AI agents."""

    def __init__(
        self,
        data_dir: str,
        levels: str = "all",
        use_mirror: bool = False,
    ) -> None:
        """Initialize the GAIABenchmark

        Args:
            data_dir (`str`):
                The directory where the dataset is downloaded and saved.
            levels (`str`):
                The levels of tasks to evaluate.
            use_mirror (`bool`, defaults to `False`):
                Whether to enable the HuggingFace mirror, which is useful for
                users in China.
        """
        super().__init__(
            name="GAIABench",
            description="The GAIA benchmark for evaluating AI agents.",
        )

        self.data_dir = os.path.abspath(data_dir)
        self.levels = levels
        self.use_mirror = use_mirror

        if os.path.exists(data_dir) and not os.path.isdir(data_dir):
            raise RuntimeError(
                f"The data_dir `{data_dir}` is not a valid directory path.",
            )

        os.makedirs(data_dir, exist_ok=True)

        self.dataset = self._load_data()

    def _load_data(self) -> list:
        """Load the dataset from the data directory."""
        valid_dir = Path(self.data_dir) / "2023/validation"
        test_dir = Path(self.data_dir) / "2023/test"

        if not valid_dir.exists() or not test_dir.exists():
            self._download_data()

        dataset = {}
        for path, label in zip([valid_dir, test_dir], ["validation", "test"]):
            dataset[label] = []
            with open(path / "metadata.jsonl", "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines:
                    data = json.loads(line)
                    if data["file_name"]:
                        data["file_name"] = path / data["file_name"]
                    dataset[label].append(data)

        def parse_levels(levels: str | int | list) -> list[int]:
            """parse levels"""
            if levels == "all":
                return [1, 2, 3]
            if isinstance(levels, int):
                return [levels]
            if isinstance(levels, list):
                return [int(_) for _ in levels]
            return [int(levels)]

        levels = parse_levels(self.levels)

        datas = [
            data for data in dataset["validation"] if data["Level"] in levels
        ]
        return datas

    def _download_data(self) -> None:
        """Download the data from the URL"""
        if self.use_mirror:
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

        from huggingface_hub import snapshot_download, login

        login(token=os.getenv("HF_TOKEN"))

        snapshot_download(
            repo_id="gaia-benchmark/GAIA",
            repo_type="dataset",
            local_dir=self.data_dir,
        )

    @staticmethod
    def _data_to_task(item: dict) -> Task:
        """Convert a dataset item to a Task object."""
        # Start the simulated phone and load initial configuration

        file_path = item["file_name"]
        if file_path:
            file_path = str(Path(file_path).resolve())

        return Task(
            id=item["task_id"],
            input=item["Question"],
            ground_truth=item["Final answer"],
            metrics=[
                GAIAAccuracy(item["Final answer"]),
            ],
            metadata={
                # The provided tools for this task, used to equip the agent
                "Annotator Metadata": item["Annotator Metadata"],
                "file_path": file_path,
            },
        )

    def __iter__(self) -> Generator[Task, None, None]:
        """Iterate over the benchmark."""
        for item in self.dataset:
            yield self._data_to_task(item)

    def __getitem__(self, index: int) -> Task:
        """Get a task by index."""
        return self._data_to_task(self.dataset[index])

    def __len__(self) -> int:
        """Get the length of the benchmark."""
        return len(self.dataset)
