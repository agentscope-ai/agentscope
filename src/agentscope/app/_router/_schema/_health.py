# -*- coding: utf-8 -*-
"""The service health report, used as DTO layer."""

from typing import Literal

from pydantic import BaseModel, Field

ComponentStatus = Literal["ok", "not_ready", "disabled"]


class HealthResponse(BaseModel):
    """The service health response."""

    status: Literal["ok", "not_ready"] = Field(
        description="Overall service status. ``not_ready`` when any "
        "required component is missing; disabled optional features do "
        "not affect it.",
    )
    version: str = Field(
        description="The API version this app was created with.",
    )
    components: dict[str, ComponentStatus] = Field(
        description="Per-subsystem readiness. ``disabled`` marks an "
        "optional feature the deployment did not enable.",
    )
