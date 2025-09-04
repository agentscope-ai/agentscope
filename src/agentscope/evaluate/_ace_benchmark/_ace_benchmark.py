# -*- coding: utf-8 -*-
"""The ACE benchmark class in agentscope. The code is implemented with
reference to the `ACEBench <https://github.com/ACEBench/ACEBench>`_
under the MIT license."""
import json
import os
from typing import Generator

import json5
import requests
from tqdm import tqdm

from ._ace_metric import ACEAccuracy, ACEProcessAccuracy
from ._ace_tools_zh import ACEPhone
from .._benchmark_base import BenchmarkBase
from .._task import Task


class ACEBenchmark(BenchmarkBase):
    """The ACE benchmark for evaluating AI agents."""

    data_dir_url: str = (
        "https://raw.githubusercontent.com/ACEBench/ACEBench/main/data_all"
    )
    """The URL to the data dir"""

    data_subdir: list[str] = [
        # "data_en",  # TODO: enable English version
        "data_zh",
    ]

    ground_truth_dir: str = "possible_answer"

    data_files: list[str] = [
        "data_agent_multi_step.json",
        "data_agent_multi_turn.json",
        # "data_normal_atom_bool.json",
        # "data_normal_atom_enum.json",
        # "data_normal_atom_list.json",
        # "data_normal_atom_number.json",
        # "data_normal_atom_object_deep.json",
        # "data_normal_atom_object_short.json",
        #
        # "data_normal_multi_turn_user_adjust.json",
        # "data_normal_multi_turn_user_switch.json",
        #
        # "data_normal_preference.json",
        # "data_normal_similar_api.json",
        # "data_normal_single_turn_parallel_function.json",
        # "data_normal_single_turn_single_function.json",
        #
        # "data_special_error_param.json",
        # "data_special_incomplete.json",
        # "data_special_irrelevant.json",
    ]
    """The data filenames"""

    def __init__(
        self,
        data_dir: str,
    ) -> None:
        """Initialize the ACEBenchmark

        Args:
            data_dir (`str`):
                The directory where the dataset is downloaded and saved.
        """
        super().__init__(
            name="ACEBench",
            description="The ACE benchmark for evaluating AI agents.",
        )

        self.data_dir = os.path.abspath(data_dir)

        if os.path.exists(data_dir) and not os.path.isdir(data_dir):
            raise RuntimeError(
                f"The data_dir `{data_dir}` is not a valid directory path.",
            )

        os.makedirs(data_dir, exist_ok=True)

        if not self._verify_data():
            self._download_data()

        self.dataset = self._load_data()

    def _load_data(self) -> list[dict]:
        """Load the dataset from the data directory."""
        dataset = []
        for subdir in self.data_subdir:
            for filename in self.data_files:
                file_path = os.path.join(self.data_dir, subdir, filename)

                gt_path = os.path.join(
                    self.data_dir,
                    subdir,
                    self.ground_truth_dir,
                    filename,
                )
                gt_dataset = {}
                with open(gt_path, "r", encoding="utf-8") as gt_file:
                    for line in gt_file:
                        gt_data = json5.loads(line)
                        gt_dataset[gt_data["id"]] = gt_data

                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        data = json5.loads(line)
                        gt = gt_dataset[data["id"]]
                        gt.pop("id", None)
                        data["ground_truth"] = gt["ground_truth"]
                        data["mile_stone"] = gt["mile_stone"]
                        data["language"] = subdir.rsplit(
                            "_",
                            maxsplit=1,
                        )[-1]
                        data["tags"] = {
                            "language": data["language"],
                            "category": filename.split(
                                ".",
                                maxsplit=1,
                            )[0].removeprefix(
                                "data_",
                            ),
                        }
                        dataset.append(data)

        return dataset

    def _verify_data(self) -> bool:
        """Verify the data completeness and integrity."""
        for subdir in self.data_subdir:
            for filename in self.data_files:
                file_path = os.path.join(self.data_dir, subdir, filename)
                if not os.path.exists(file_path):
                    return False

                gt_path = os.path.join(
                    self.data_dir,
                    subdir,
                    self.ground_truth_dir,
                    filename,
                )
                if not os.path.exists(gt_path):
                    return False

        return True

    def _download_data(self) -> None:
        """Download the data from the URL"""
        for subdir in self.data_subdir:
            subdir_path = os.path.join(self.data_dir, subdir)
            subdir_gt_path = os.path.join(subdir_path, self.ground_truth_dir)
            os.makedirs(subdir_path, exist_ok=True)
            os.makedirs(subdir_gt_path, exist_ok=True)
            for filename in tqdm(
                self.data_files,
                desc=f"Downloading {subdir}",
            ):
                response = requests.get(
                    f"{self.data_dir_url}/{subdir}/{filename}",
                )
                response.raise_for_status()
                with open(os.path.join(subdir_path, filename), "wb") as f:
                    f.write(response.content)

                gt_response = requests.get(
                    f"{self.data_dir_url}/{subdir}/"
                    f"{self.ground_truth_dir}/{filename}",
                )
                gt_response.raise_for_status()
                with open(os.path.join(subdir_gt_path, filename), "wb") as f:
                    f.write(gt_response.content)

    @staticmethod
    def _data_to_task(item: dict) -> Task:
        """Convert a dataset item to a Task object."""
        # Start the simulated phone and load initial configuration
        ace_phone = ACEPhone()
        ace_phone.load_initial_config(item["initial_config"])

        # Obtain tool functions
        tools: list[tuple] = []
        for function_schema in item["function"]:
            name = function_schema["name"]

            # Handle the schema differences
            formatted_schema = json.loads(
                json.dumps(
                    function_schema,
                ).replace(
                    '"type": "dict"',
                    '"type": "object"',
                ),
            )

            tool_function = ace_phone.get_tool_function(name)
            tools.append(
                (
                    tool_function,
                    {
                        "type": "function",
                        "function": formatted_schema,
                    },
                ),
            )

        return Task(
            id=item["id"],
            input=item["question"],
            ground_truth={
                "state": item["ground_truth"],
                "mile_stone": item.get("mile_stone", []),
            },
            tags=item.get("tags", {}),
            metrics=[
                ACEAccuracy(item["ground_truth"]),
                ACEProcessAccuracy(item["mile_stone"]),
            ],
            metadata={
                # The phone is used to extract the final state after finishing
                # the task.
                "phone": ace_phone,
                # The provided tools for this task, used to equip the agent
                "tools": tools,
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
