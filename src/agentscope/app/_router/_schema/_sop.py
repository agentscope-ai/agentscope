# -*- coding: utf-8 -*-
"""Request / response schemas for the SOP router."""
from pydantic import BaseModel, Field

from ...storage import SOPData, SOPRecord, SOPRunRecord
from ....message import Msg
from ....sop import SOPPhase


class SOPSchemaResponse(BaseModel):
    """The JSON Schema a procedure's editor is built from."""

    schema_: dict = Field(
        alias="schema",
        description="Flattened JSON Schema of ``SOPData``.",
    )


class CreateSOPRequest(BaseModel):
    """Request body for creating a procedure."""

    data: SOPData = Field(description="The procedure to store.")


class CreateSOPResponse(BaseModel):
    """Response body after creating a procedure."""

    sop_id: str = Field(description="Server-assigned procedure identifier.")


class UpdateSOPRequest(BaseModel):
    """Request body for replacing a procedure's contents."""

    data: SOPData = Field(description="The procedure as it should now read.")


class ListSOPsResponse(BaseModel):
    """Response body listing the caller's procedures."""

    sops: list[SOPRecord] = Field(description="The procedures.")
    total: int = Field(description="How many there are.")


class StartSOPRunRequest(BaseModel):
    """Request body for starting a run."""

    inputs: list[Msg] = Field(
        default_factory=list,
        description="What the run is started with, read by its first step.",
    )


class ListSOPRunsResponse(BaseModel):
    """Response body listing runs."""

    runs: list[SOPRunRecord] = Field(description="The runs, newest first.")
    total: int = Field(description="How many were returned.")


class SOPRunResponse(BaseModel):
    """Response body carrying one run and where it stands."""

    run: SOPRunRecord = Field(description="The run.")
    phase: SOPPhase = Field(description="Where it stands overall.")


class SubmitVerdictRequest(BaseModel):
    """Request body for a person's verdict on a step."""

    step_index: int = Field(
        ge=0,
        description="Which step is being judged, by its position.",
    )
    passed: bool = Field(description="Whether the attempt is accepted.")
    message: str = Field(
        default="",
        description=(
            "Why it was refused. Handed to the author verbatim on the "
            "next attempt."
        ),
    )
