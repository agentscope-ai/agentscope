# -*- coding: utf-8 -*-
"""An AgentLoop (SLS) based evaluator storage that extends FileEvaluatorStorage
to additionally write evaluation results to Alibaba Cloud SLS.

Refer to:
https://help.aliyun.com/zh/sls/developer-reference/write-log
"""

import json
import time
import uuid
from typing import Any

from ..._logging import logger
from ._file_evaluator_storage import FileEvaluatorStorage
from .._solution import SolutionOutput
from .._agentloop_config import AgentLoopConfig
from ...types import JSONSerializableObject

# Fixed logstore name for experiment results
EXPERIMENT_LOGSTORE = "experiment_detail"


class AgentLoopEvaluatorStorage(FileEvaluatorStorage):
    """AgentLoop (SLS) based evaluator storage that extends
    FileEvaluatorStorage.

    This storage implementation inherits all functionality from
    FileEvaluatorStorage for local file operations, and additionally writes
    evaluation results to SLS using the PutLogs API.

    The logs written to SLS follow the AgentLoop experiment schema with fields
    like experiment_id, experiment_type, experiment_data, experiment_result,
    experiment_metrics, etc.
    """

    def __init__(
        self,
        save_dir: str,
        config: AgentLoopConfig,
        experiment_id: str | None = None,
        experiment_name: str | None = None,
        experiment_type: str = "agent",
        experiment_metadata: dict | None = None,
        experiment_config: dict | None = None,
    ) -> None:
        """Initialize the AgentLoop evaluator storage.

        Args:
            save_dir (`str`):
                The directory to save evaluation results locally.
            config (`AgentLoopConfig`):
                The AgentLoop configuration containing workspace, dataset,
                region_id, project, and credentials.
            experiment_id (`str | None`):
                The unique experiment ID. If not provided, a UUID will be
                generated automatically.
            experiment_name (`str | None`):
                A human-readable name for the experiment. If not provided,
                will be generated based on experiment_id and timestamp.
            experiment_type (`str`):
                The experiment type. Either "model" or "agent".
                Defaults to "agent".
            experiment_metadata (`dict | None`):
                Additional experiment metadata, e.g. {"run_env": "local_run"}.
                Defaults to None.
            experiment_config (`dict | None`):
                The experiment configuration, e.g. model settings or agent
                configuration. Defaults to None.
        """
        # Initialize parent class
        super().__init__(save_dir=save_dir)

        # Store config reference
        self.config = config
        self.config.validate_credentials()

        self.logstore = EXPERIMENT_LOGSTORE

        # Query project from workspace if not provided in config
        if not self.config.project:
            self.config.project = self._get_workspace_project()

        # Experiment identification
        self.experiment_id = experiment_id or str(uuid.uuid4())
        self.experiment_name = experiment_name or (
            f"experiment_{self.experiment_id[:8]}_"
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self.experiment_type = experiment_type
        self.experiment_start_time = int(time.time() * 1000)
        self.experiment_metadata = experiment_metadata or {
            "run_env": "local_run",
        }
        self.experiment_config = experiment_config or {}

        # Cache for task meta (experiment_data), keyed by task_id
        self._task_meta_cache: dict[str, dict] = {}

    def _get_cms_client(self) -> Any:
        """Get the CMS client instance.

        Returns:
            `Cms20240330Client`:
                The CMS client instance.
        """
        try:
            from alibabacloud_cms20240330.client import Client
            from alibabacloud_tea_openapi import models as open_api_models
        except ImportError as e:
            raise ImportError(
                "The alibabacloud-cms20240330-inner package is required for "
                "AgentLoopEvaluatorStorage. Install it with: "
                "pip install alibabacloud-cms20240330-inner",
            ) from e

        client_config = open_api_models.Config(
            access_key_id=self.config.access_key_id,
            access_key_secret=self.config.access_key_secret,
        )
        client_config.endpoint = self.config.cms_endpoint

        return Client(client_config)

    def _get_workspace_project(self) -> str:
        """Get the SLS project from workspace.

        Returns:
            `str`:
                The SLS project name.
        """
        try:
            from alibabacloud_tea_util import models as util_models
        except ImportError as e:
            raise ImportError(
                "The alibabacloud-cms20240330-inner package is required for "
                "AgentLoopEvaluatorStorage. Install it with: "
                "pip install alibabacloud-cms20240330-inner",
            ) from e

        client = self._get_cms_client()
        runtime = util_models.RuntimeOptions()
        headers: dict = {}

        resp = client.get_workspace_with_options(
            self.config.workspace,
            headers,
            runtime,
        )
        logger.debug(
            f"CMS workspace response: "
            f"{json.dumps(resp.to_map(), default=str, indent=2)}",
        )

        # Extract project from response
        body = resp.body
        if body and body.sls_project:
            return body.sls_project

        raise ValueError(
            f"Failed to get SLS project for workspace "
            f"'{self.config.workspace}'. "
            "Please ensure the workspace exists and has SLS configured.",
        )

    def _get_sls_client(self) -> Any:
        """Get the SLS LogClient instance.

        Returns:
            `LogClient`:
                The SLS LogClient instance.
        """
        try:
            from aliyun.log import LogClient
        except ImportError as e:
            raise ImportError(
                "The aliyun-log-python-sdk package is required for "
                "AgentLoopEvaluatorStorage. Install it with: "
                "pip install aliyun-log-python-sdk",
            ) from e

        return LogClient(
            self.config.sls_endpoint,
            self.config.access_key_id,
            self.config.access_key_secret,
        )

    def _put_log(self, contents: list[tuple[str, str]]) -> None:
        """Write a log entry to SLS.

        Args:
            contents (`list[tuple[str, str]]`):
                The log contents as key-value pairs.
        """
        try:
            from aliyun.log import LogItem, PutLogsRequest
        except ImportError as e:
            raise ImportError(
                "The aliyun-log-python-sdk package is required for "
                "AgentLoopEvaluatorStorage. Install it with: "
                "pip install aliyun-log-python-sdk",
            ) from e

        client = self._get_sls_client()

        log_item = LogItem()
        log_item.set_time(int(time.time()))
        log_item.set_contents(contents)

        request = PutLogsRequest(
            self.config.project,
            self.logstore,
            "",  # topic
            "",  # source
            [log_item],
        )
        client.put_logs(request)

    def _build_base_log_contents(
        self,
        task_id: str | None = None,
        repeat_id: str | None = None,
    ) -> list[tuple[str, str]]:
        """Build the base log contents with common fields.

        Args:
            task_id (`str | None`):
                The task ID if applicable.
            repeat_id (`str | None`):
                The repeat ID if applicable.

        Returns:
            `list[tuple[str, str]]`:
                A list of key-value tuples for log contents.
        """
        contents = [
            ("experiment_id", self.experiment_id),
            ("experiment_name", self.experiment_name),
            ("experiment_start_time", str(self.experiment_start_time)),
            ("experiment_type", self.experiment_type),
            (
                "experiment_metadata",
                json.dumps(self.experiment_metadata, ensure_ascii=False),
            ),
            (
                "experiment_config",
                json.dumps(self.experiment_config, ensure_ascii=False),
            ),
        ]

        if task_id is not None:
            contents.append(("task_id", task_id))
        if repeat_id is not None:
            contents.append(("repeat_id", repeat_id))

        return contents

    def save_solution_result(
        self,
        task_id: str,
        repeat_id: str,
        output: SolutionOutput,
        **kwargs: Any,
    ) -> None:
        """Save the solution result to both local file and SLS.

        This method writes the complete experiment record to SLS, including:
        - experiment_data: from cached task meta
        - data_config: dataset configuration
        - experiment_result: the solution output

        Args:
            task_id (`str`):
                The task ID.
            repeat_id (`str`):
                The repeat ID for the task.
            output (`SolutionOutput`):
                The solution output to be saved.
        """
        # First, save to local file using parent class
        super().save_solution_result(
            task_id=task_id,
            repeat_id=repeat_id,
            output=output,
            **kwargs,
        )

        # Then, write to SLS
        contents = self._build_base_log_contents(
            task_id=task_id,
            repeat_id=repeat_id,
        )
        # Get cached experiment_data
        experiment_data = self._task_meta_cache.get(task_id, {})

        # Add data_config field
        data_config = {
            "data_type": "dataset",
            "project": self.config.project,
            "dataset_id": self.config.dataset,
            "dataset_item_id": experiment_data.get("input", {}).get(
                "id",
                task_id,
            ),
        }
        contents.append(
            (
                "data_config",
                json.dumps(data_config, ensure_ascii=False),
            ),
        )

        # Add experiment_data field
        contents.append(
            (
                "experiment_data",
                json.dumps(
                    experiment_data.get("input", {}),
                    ensure_ascii=False,
                    default=str,
                ),
            ),
        )

        # Add experiment_result field with output data
        result_data = {
            "success": output.success,
            "output": output.output,
            "trajectory": output.trajectory,
            "meta": output.meta,
        }
        contents.append(
            (
                "experiment_output",
                json.dumps(result_data, ensure_ascii=False, default=str),
            ),
        )
        self._put_log(contents)

    def save_task_meta(
        self,
        task_id: str,
        meta_info: dict[str, JSONSerializableObject],
    ) -> None:
        """Save the task meta information to local file and cache for
        later use.

        The task meta (experiment_data) is cached and will be written to SLS
        together with the solution result in save_solution_result().

        Args:
            task_id (`str`):
                The task ID.
            meta_info (`dict[str, JSONSerializableObject]`):
                The task meta information to be saved.
        """
        # Save to local file using parent class
        super().save_task_meta(
            task_id=task_id,
            meta_info=meta_info,
        )

        # Cache the meta_info for later use in save_solution_result
        self._task_meta_cache[task_id] = meta_info
