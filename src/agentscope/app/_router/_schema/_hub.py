# -*- coding: utf-8 -*-
"""Hub schemas shared by the MCP and skill hub routes."""
from pydantic import BaseModel, Field


class HubInfo(BaseModel):
    """One registered hub, as shown in the hub picker."""

    hub_id: str = Field(description="The id addressing this hub.")
    display_name: str = Field(description="The user-facing hub name.")
    description: str = Field(description="The user-facing description.")
    icon_url: str | None = Field(
        default=None,
        description="An image identifying the hub, when it has one.",
    )
