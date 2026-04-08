# -*- coding: utf-8 -*-
"""Configuration class for AgentLoop components."""
import os
from dataclasses import dataclass, field


@dataclass
class AgentLoopConfig:
    """Configuration for AgentLoop benchmark and evaluator storage.

    This class encapsulates all the configuration needed to connect to
    Alibaba Cloud CMS (Cloud Monitor Service) and SLS (Simple Log Service)
    for the AgentLoop evaluation framework.

    Attributes:
        workspace (`str`):
            The CMS workspace name.
        dataset (`str`):
            The CMS dataset name.
        region_id (`str`):
            The Alibaba Cloud region ID (e.g., "cn-hangzhou", "cn-shanghai").
            Used to construct endpoints.
        project (`str`):
            The SLS project name. If not provided, will be queried from
            the workspace at runtime.
        query (`str`):
            Custom SQL query for loading data. When provided, the query is
            executed as-is in a single call. When empty (the default),
            automatic LIMIT/OFFSET pagination is used to load up to
            ``max_rows`` records.
        max_rows (`int`):
            Maximum number of records to load when using automatic pagination
            (i.e., when ``query`` is empty). Defaults to 1000. Ignored when
            a custom ``query`` is provided.
        ground_truth_field (`str`):
            The field name in the dataset that contains the ground truth.
            If not provided, ground_truth will be empty string.
        access_key_id (`str`):
            The Alibaba Cloud AccessKey ID. If not provided, will try to
            get from environment variable `ALIBABA_CLOUD_ACCESS_KEY_ID`.
        access_key_secret (`str`):
            The Alibaba Cloud AccessKey Secret. If not provided, will try
            to get from environment variable `ALIBABA_CLOUD_ACCESS_KEY_SECRET`.
    """

    workspace: str
    dataset: str
    region_id: str
    project: str = ""
    query: str = ""
    max_rows: int = 1000
    ground_truth_field: str = ""
    access_key_id: str = field(default="", repr=False)
    access_key_secret: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        """Post-initialization to load credentials and set defaults."""
        if not self.access_key_id:
            self.access_key_id = os.environ.get(
                "ALIBABA_CLOUD_ACCESS_KEY_ID",
                "",
            )
        if not self.access_key_secret:
            self.access_key_secret = os.environ.get(
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
                "",
            )
        if self.max_rows <= 0:
            raise ValueError(
                f"`max_rows` must be a positive integer, got {self.max_rows}.",
            )

    def validate_credentials(self) -> None:
        """Validate that required credentials are present.

        Raises:
            ValueError: If AccessKey credentials are missing.
        """
        if not self.access_key_id or not self.access_key_secret:
            raise ValueError(
                "AccessKey credentials are required. Provide them via "
                "`access_key_id` and `access_key_secret` parameters or set "
                "`ALIBABA_CLOUD_ACCESS_KEY_ID` and "
                "`ALIBABA_CLOUD_ACCESS_KEY_SECRET` environment variables.",
            )

    @property
    def cms_endpoint(self) -> str:
        """Get the CMS endpoint URL.

        Returns:
            `str`: The CMS endpoint URL.
        """
        return f"cms.{self.region_id}.aliyuncs.com"

    @property
    def sls_endpoint(self) -> str:
        """Get the SLS endpoint URL.

        Returns:
            `str`: The SLS endpoint URL.
        """
        return f"{self.region_id}.log.aliyuncs.com"