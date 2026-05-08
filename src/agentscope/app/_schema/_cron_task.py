# -*- coding: utf-8 -*-
"""The cron task config class, used for storage."""
import shortuuid
from pydantic import BaseModel, Field


class CronTaskConfig(BaseModel):
    """The cron task config."""

    id: str = Field(
        description="The cron task id",
        default_factory=lambda: shortuuid.uuid(),
    )

    name: str = Field(
        title=" Name",
        description="The cron task name",
    )

    description: str = Field(
        title=" Description",
        description="The cron task description",
        max_length=500,
    )

    cron_expression: str = Field(
        title="Cron Expression",
        description="The cron expression",
    )
