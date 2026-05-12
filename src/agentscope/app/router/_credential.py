# -*- coding: utf-8 -*-
"""Credential router for managing API keys of model providers."""
import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..storage import CredentialRecord

credential_router = APIRouter(
    prefix="/credential",
    tags=["credential"],
    responses={404: {"description": "Not found"}},
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CredentialListResponse(BaseModel):
    """Response model for listing credentials."""

    credentials: list[CredentialRecord] = Field(
        title="Credentials",
        description="List of stored credentials.",
    )

    total: int = Field(description="Total number of credentials.")


class CreateCredentialRequest(BaseModel):
    """Request body for creating a new credential."""




class CreateCredentialResponse(BaseModel):
    """Response model after creating a credential."""

    credential_id: str = Field(
        description="Unique identifier of the newly created credential.",
    )



class UpdateCredentialRequest(BaseModel):
    """Request body for updating an existing credential.

    All fields are optional; omit any field to leave it unchanged.
    """


class UpdateCredentialResponse(BaseModel):
    """Response model after updating a credential."""

    credential_id: str = Field(description="Credential identifier.")
    name: str = Field(description="Updated display name.")
    provider: str = Field(description="Updated provider.")
    api_key_masked: str = Field(
        description="Masked API key after the update.",
    )
    description: str = Field(description="Updated description.")
    metadata: dict[str, Any] = Field(description="Updated metadata.")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _mask_api_key(api_key: str) -> str:
    """Return a masked version of *api_key*, showing only the last 4 chars.

    Args:
        api_key (`str`):
            The plain-text API key to mask.

    Returns:
        `str`:
            The masked key, e.g. ``"****abcd"``.
    """
    if len(api_key) <= 4:
        return "*" * len(api_key)
    return "*" * (len(api_key) - 4) + api_key[-4:]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@credential_router.get(
    "/",
    response_model=CredentialListResponse,
    summary="List all credentials",
)
async def list_credentials() -> CredentialListResponse:
    """Return a list of all stored credentials with masked API keys.

    Returns:
        `CredentialListResponse`:
            The list of credentials and the total count.
    """
    # TODO: query storage / credential registry to retrieve all credentials
    credentials: list[CredentialInfo] = []
    return CredentialListResponse(
        credentials=credentials,
        total=len(credentials),
    )


@credential_router.post(
    "/",
    response_model=CreateCredentialResponse,
    status_code=201,
    summary="Create a new credential",
)
async def create_credential(
    body: CreateCredentialRequest,
) -> CreateCredentialResponse:
    """Store a new API key credential.

    The plain-text ``api_key`` must be supplied in the request body and is
    stored securely by the backend.  It is **never** returned in plain-text;
    only the masked form is echoed back.

    Args:
        body (`CreateCredentialRequest`):
            Request body containing name, provider, and API key.

    Returns:
        `CreateCredentialResponse`:
            The identifier and masked key of the newly created credential.
    """
    credential_id = uuid.uuid4().hex
    # TODO: encrypt / hash the api_key before persisting
    # TODO: persist the credential record to storage / credential registry
    return CreateCredentialResponse(
        credential_id=credential_id,
        name=body.name,
        provider=body.provider,
        api_key_masked=_mask_api_key(body.api_key),
    )


@credential_router.delete(
    "/{credential_id}",
    status_code=204,
    summary="Delete a credential",
)
async def delete_credential(credential_id: str) -> None:
    """Delete a stored credential permanently.

    Args:
        credential_id (`str`):
            The unique identifier of the credential to delete.

    Raises:
        `HTTPException`:
            404 if the credential does not exist.
    """
    # TODO: verify credential exists; raise HTTPException(status_code=404) if not found
    # TODO: delete the credential record from storage / credential registry


@credential_router.patch(
    "/{credential_id}",
    response_model=UpdateCredentialResponse,
    summary="Update a credential",
)
async def update_credential(
    credential_id: str,
    body: UpdateCredentialRequest,
) -> UpdateCredentialResponse:
    """Partially update an existing credential.

    Only the fields present in the request body are updated; all other fields
    keep their current values.

    Args:
        credential_id (`str`):
            The unique identifier of the credential to update.
        body (`UpdateCredentialRequest`):
            Fields to update.

    Returns:
        `UpdateCredentialResponse`:
            The credential identifier with its updated fields.

    Raises:
        `HTTPException`:
            404 if the credential does not exist.
    """
    # TODO: load the existing credential; raise HTTPException(status_code=404) if not found
    # TODO: if body.api_key is provided, encrypt / hash the new key before persisting
    # TODO: apply partial updates and persist the changes to storage

    # Placeholder values – will be replaced by the actual persisted record
    updated_name = body.name or ""
    updated_provider = body.provider or ""
    updated_api_key_masked = (
        _mask_api_key(body.api_key) if body.api_key else "****"
    )
    updated_description = body.description or ""
    updated_metadata = body.metadata or {}

    return UpdateCredentialResponse(
        credential_id=credential_id,
        name=updated_name,
        provider=updated_provider,
        api_key_masked=updated_api_key_masked,
        description=updated_description,
        metadata=updated_metadata,
    )
