# -*- coding: utf-8 -*-
"""Request / response schemas for the credential router."""
from pydantic import BaseModel, Field

from ..._service import CredentialView


class CreateCredentialRequest(BaseModel):
    """Request body for creating a new credential."""

    data: dict = Field(description="Credential payload (e.g. API keys).")


class CreateCredentialResponse(BaseModel):
    """Response body after creating a credential."""

    credential_id: str = Field(
        description="Server-assigned credential identifier.",
    )


class UpdateCredentialRequest(BaseModel):
    """Request body for updating an existing credential."""

    data: dict = Field(description="New credential payload.")


class ListCredentialsResponse(BaseModel):
    """Response body for listing credentials."""

    credentials: list[CredentialView] = Field(
        description="Credential records.",
    )
    total: int = Field(description="Total number of credentials.")


class ListCredentialSchemasResponse(BaseModel):
    """Response body for listing credential type schemas."""

    schemas: list[dict] = Field(
        description="JSON schemas for all registered credential types.",
    )
