# -*- coding: utf-8 -*-
"""The AgentLoop benchmark class for loading evaluation datasets from CMS."""

import json
import uuid
from typing import Any, Generator

from ..._logging import logger
from .._benchmark_base import BenchmarkBase
from .._task import Task
from .._agentloop_config import AgentLoopConfig


class AgentLoopBenchmark(BenchmarkBase):
    """The AgentLoop benchmark for loading evaluation datasets from
    Alibaba Cloud CMS (Cloud Monitor Service).

    This benchmark allows users to load evaluation datasets directly from
    CMS by specifying the workspace and dataset names. The data is queried
    using the CMS SDK and converted to Task objects using a user-provided
    converter function.

    Install the required SDK:
        pip install -i http://yum.tbsite.net/aliyun-pypi/simple/ \\
            --extra-index-url http://yum.tbsite.net/pypi/simple/ \\
            --trusted-host=yum.tbsite.net \\
            alibabacloud-cms20240330-inner==6.0.8
    """

    def __init__(
        self,
        config: AgentLoopConfig,
        name: str = "AgentLoopBenchmark",
        description: str = "Benchmark loaded from AgentLoop.",
    ) -> None:
        """Initialize the AgentLoopBenchmark.

        Args:
            config (`AgentLoopConfig`):
                The AgentLoop configuration containing workspace, dataset,
                region_id, query, and credentials.
            name (`str`):
                The name of this benchmark. Defaults to "AgentLoopBenchmark".
            description (`str`):
                A brief description of this benchmark.
                Defaults to "Benchmark loaded from CMS.".
        """
        super().__init__(name=name, description=description)

        self.config = config
        self.config.validate_credentials()

        # Define task_converter
        def task_converter(log_record: dict) -> Task:
            # Generate a random ID for each task
            task_id = str(uuid.uuid4())
            # Get ground_truth from configured field,
            # empty string if not configured
            ground_truth = ""
            if config.ground_truth_field:
                ground_truth = log_record.get(config.ground_truth_field, "")

            return Task(
                id=task_id,
                input=log_record,
                ground_truth=ground_truth,
                metrics=[],
                metadata={},
            )

        # Load dataset from CMS and convert to Task objects
        # Convert once and cache to ensure stable task IDs
        raw_dataset = self._load_data_from_cms()
        self._tasks: list[Task] = [
            task_converter(item) for item in raw_dataset
        ]

    def _get_client(self) -> Any:
        """Get the CMS client instance.

        Returns:
            `Cms20240330Client`:
                The CMS client instance.

        Raises:
            `ImportError`:
                If the alibabacloud-cms20240330-inner package is not installed.
        """
        try:
            from alibabacloud_cms20240330.client import Client
            from alibabacloud_tea_openapi import models as open_api_models
        except ImportError as e:
            raise ImportError(
                "The alibabacloud-cms20240330-inner package is required for "
                "AgentLoopBenchmark. Install it with: "
                "pip install alibabacloud-cms20240330-inner==6.0.8",
            ) from e

        client_config = open_api_models.Config(
            access_key_id=self.config.access_key_id,
            access_key_secret=self.config.access_key_secret,
        )
        client_config.endpoint = self.config.cms_endpoint

        return Client(client_config)

    def _load_data_from_cms(self) -> list[dict]:
        """Load the dataset from CMS using ExecuteQuery API.

        Returns:
            `list[dict]`:
                A list of data records loaded from CMS.
        """
        try:
            from alibabacloud_cms20240330 import (
                models as cms_20240330_models,
            )
        except ImportError as e:
            raise ImportError(
                "The alibabacloud-cms20240330-inner package is required for "
                "AgentLoopBenchmark. Install it with: "
                "pip install alibabacloud-cms20240330-inner==6.0.8",
            ) from e

        client = self._get_client()

        req = cms_20240330_models.ExecuteQueryRequest(
            type="SQL",
            query=self.config.query,
        )

        logger.info(f"CMS query: {self.config.query}")
        logger.info(
            f"CMS workspace: {self.config.workspace}, "
            f"dataset: {self.config.dataset}",
        )

        resp = client.execute_query(
            self.config.workspace,
            self.config.dataset,
            req,
        )
        logger.debug(json.dumps(resp.to_map(), default=str, indent=2))

        # Parse response data
        dataset = []
        if resp.body and resp.body.data:
            # The response data structure may vary, handle accordingly
            data = resp.body.data
            if isinstance(data, list):
                dataset = data
            elif isinstance(data, dict):
                # If data is a dict with rows/records
                if "rows" in data:
                    dataset = data["rows"]
                elif "records" in data:
                    dataset = data["records"]
                else:
                    dataset = [data]

        return dataset

    def __iter__(self) -> Generator[Task, None, None]:
        """Iterate over the benchmark, yielding Task objects.

        Yields:
            `Task`:
                Task objects converted from data records.
        """
        for task in self._tasks:
            yield task

    def __getitem__(self, index: int) -> Task:
        """Get a task by index.

        Args:
            index (`int`):
                The index of the task.

        Returns:
            `Task`:
                The Task object at the given index.
        """
        return self._tasks[index]

    def __len__(self) -> int:
        """Get the number of tasks in the benchmark.

        Returns:
            `int`:
                The number of tasks.
        """
        return len(self._tasks)
