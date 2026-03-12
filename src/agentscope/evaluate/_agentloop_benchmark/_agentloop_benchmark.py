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
        pip install alibabacloud-cms20240330-inner
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
                "pip install alibabacloud-cms20240330-inner",
            ) from e

        client_config = open_api_models.Config(
            access_key_id=self.config.access_key_id,
            access_key_secret=self.config.access_key_secret,
        )
        client_config.endpoint = self.config.cms_endpoint

        return Client(client_config)

    def _parse_response_data(self, data: Any) -> list[dict]:
        """Parse the data field from a CMS ExecuteQuery response.

        Args:
            data:
                The ``resp.body.data`` value returned by the CMS API.

        Returns:
            `list[dict]`:
                A list of parsed data records.
        """
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "rows" in data:
                return data["rows"]
            if "records" in data:
                return data["records"]
            logger.warning(
                "CMS response data is a dict but contains neither 'rows' nor "
                "'records' key. Treating the entire dict as a single record. "
                f"Keys present: {list(data.keys())}",
            )
            return [data]
        logger.warning(
            "Unexpected CMS response data type: %s. Returning empty dataset.",
            type(data).__name__,
        )
        return []

    # Number of records fetched per paginated request
    _PAGE_SIZE: int = 100

    def _execute_query_request(
        self,
        client: Any,
        cms_20240330_models: Any,
        query: str,
    ) -> list[dict]:
        """Execute a single CMS ExecuteQuery request and return parsed records.

        Args:
            client:
                The CMS client instance.
            cms_20240330_models:
                The CMS models module.
            query (`str`):
                The SQL query string to execute.

        Returns:
            `list[dict]`:
                Parsed records from the response, or an empty list if the
                response contains no data.

        Raises:
            `RuntimeError`:
                If the CMS API call fails.
        """
        req = cms_20240330_models.ExecuteQueryRequest(
            type="SQL",
            query=query,
        )
        try:
            resp = client.execute_query(
                self.config.workspace,
                self.config.dataset,
                req,
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to query CMS dataset '{self.config.dataset}' "
                f"in workspace '{self.config.workspace}': {e}",
            ) from e

        logger.debug(json.dumps(resp.to_map(), default=str, indent=2))

        if resp.body and resp.body.data:
            return self._parse_response_data(resp.body.data)
        return []

    def _load_data_from_cms(self) -> list[dict]:
        """Load the dataset from CMS.

        - If ``config.query`` is set, executes it as-is in a single call.
        - Otherwise, uses LIMIT/OFFSET pagination to load up to
          ``config.max_rows`` records, stopping early when a page returns
          no data or the API raises an error.

        Returns:
            `list[dict]`:
                A list of data records loaded from CMS.

        Raises:
            `ImportError`:
                If the alibabacloud-cms20240330-inner package is not installed.
            `RuntimeError`:
                If the CMS API call fails (single-query mode only).
        """
        try:
            from alibabacloud_cms20240330 import (
                models as cms_20240330_models,
            )
        except ImportError as e:
            raise ImportError(
                "The alibabacloud-cms20240330-inner package is required for "
                "AgentLoopBenchmark. Install it with: "
                "pip install alibabacloud-cms20240330-inner",
            ) from e

        client = self._get_client()

        logger.info(
            f"CMS workspace: {self.config.workspace}, "
            f"dataset: {self.config.dataset}",
        )

        if self.config.query:
            # User-provided custom query: execute as-is in a single call
            logger.info(f"CMS custom query: {self.config.query}")
            return self._execute_query_request(
                client,
                cms_20240330_models,
                self.config.query,
            )

        # No custom query: paginate with LIMIT/OFFSET up to max_rows
        escaped_dataset = self.config.dataset.replace("`", "``")
        dataset: list[dict] = []

        while len(dataset) < self.config.max_rows:
            remaining = self.config.max_rows - len(dataset)
            page_size = min(self._PAGE_SIZE, remaining)
            offset = len(dataset)
            page_query = (
                f"* | select * from `{escaped_dataset}` "
                f"LIMIT {offset}, {page_size}"
            )
            logger.info(
                f"CMS paginated query "
                f"(offset={offset}, limit={page_size}): {page_query}",
            )
            try:
                page = self._execute_query_request(
                    client,
                    cms_20240330_models,
                    page_query,
                )
            except RuntimeError as e:
                logger.warning(
                    f"Paginated query failed at offset {offset}, "
                    f"stopping early: {e}",
                )
                break

            if not page:
                logger.info(
                    f"Empty response at offset {offset}, stopping pagination.",
                )
                break

            dataset.extend(page)
            if len(dataset) >= self.config.max_rows:
                dataset = dataset[: self.config.max_rows]
                break

        logger.info(f"Loaded {len(dataset)} records from CMS.")
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
