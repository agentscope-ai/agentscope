# -*- coding: utf-8 -*-
"""Request and response schemas for the workspace router."""

from pydantic import BaseModel, Field


class ArtifactEntry(BaseModel):
    """One file or directory inside a workspace."""

    name: str = Field(description="Base name of the artifact.")
    path: str = Field(description="Path relative to the workspace root.")
    is_directory: bool = Field(
        description="Whether the artifact is a directory.",
    )
    media_type: str | None = Field(
        default=None,
        description="Detected IANA media type for files.",
    )
    modified_at: float | None = Field(
        default=None,
        description="POSIX modification timestamp when available.",
    )


class ListArtifactsResponse(BaseModel):
    """Response body for listing artifacts in a workspace directory."""

    artifacts: list[ArtifactEntry] = Field(
        description="Immediate children of the requested directory.",
    )
    total: int = Field(description="Number of returned artifacts.")
