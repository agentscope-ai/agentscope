# -*- coding: utf-8 -*-
"""Credential router — CRUD endpoints for API key credentials."""
from fastapi import APIRouter, Depends, HTTPException, status

from .._deps import get_current_user_id, get_storage
from .._schema._credential import (
    CreateCredentialRequest,
    CreateCredentialResponse,
    CredentialListResponse,
    UpdateCredentialRequest,
)
from ..storage._base import StorageBase
from ..storage._model._credential import CredentialBase, CredentialRecord

credential_router = APIRouter(
    prefix="/credential",
    tags=["credential"],
    responses={404: {"description": "Not found"}},
)


@credential_router.get(
    "/",
    response_model=CredentialListResponse,
    summary="List all credentials",
)
async def list_credentials(
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> CredentialListResponse:
    """Return all credential records belonging to the authenticated user.

    Args:
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `CredentialListResponse`: All credential records and their total count.
    """
    credentials = await storage.list_credentials(user_id)
    return CredentialListResponse(
        credentials=credentials, total=len(credentials)
    )


@credential_router.post(
    "/",
    response_model=CreateCredentialResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new credential",
)
async def create_credential(
    body: CreateCredentialRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> CreateCredentialResponse:
    """Store a new credential.

    Args:
        body (`CreateCredentialRequest`): Credential payload to store.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `CreateCredentialResponse`: The server-assigned credential identifier.
    """
    credential_id = await storage.upsert_credential(
        user_id, CredentialBase(data=body.data)
    )
    return CreateCredentialResponse(credential_id=credential_id)


@credential_router.patch(
    "/{credential_id}",
    response_model=CredentialRecord,
    summary="Update a credential",
)
async def update_credential(
    credential_id: str,
    body: UpdateCredentialRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> CredentialRecord:
    """Replace the payload of an existing credential.

    Args:
        credential_id (`str`): The credential to update.
        body (`UpdateCredentialRequest`): New credential payload.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `CredentialRecord`: The updated credential record.

    Raises:
        `HTTPException`: 404 if the credential does not exist or does not
            belong to the authenticated user.
    """
    credentials = await storage.list_credentials(user_id)
    existing = next((c for c in credentials if c.id == credential_id), None)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credential '{credential_id}' not found.",
        )

    await storage.upsert_credential(
        user_id, CredentialBase(id=credential_id, data=body.data)
    )
    # Re-fetch to return the persisted record with updated timestamps.
    credentials = await storage.list_credentials(user_id)
    updated = next(c for c in credentials if c.id == credential_id)
    return updated


@credential_router.delete(
    "/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a credential",
)
async def delete_credential(
    credential_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> None:
    """Permanently delete a credential.

    Args:
        credential_id (`str`): The credential to delete.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Raises:
        `HTTPException`: 404 if the credential does not exist or does not
            belong to the authenticated user.
    """
    deleted = await storage.delete_credential(user_id, credential_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credential '{credential_id}' not found.",
        )
