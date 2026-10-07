# -*- coding: utf-8 -*-
"""Credential router — CRUD endpoints for API key credentials."""
from fastapi import APIRouter, Depends, HTTPException, status

from ..access import ResourceKind
from ..deps import (
    get_current_user_id,
    get_resource_access_service,
    get_storage,
)
from ._schema import (
    CreateCredentialRequest,
    CreateCredentialResponse,
    ListCredentialsResponse,
    ListCredentialSchemasResponse,
    ListRemoteModelsResponse,
    UpdateCredentialRequest,
)
from .._service import CredentialView, ResourceAccessService
from ..storage import StorageBase
from ...credential import CredentialFactory

credential_router = APIRouter(
    prefix="/credential",
    tags=["credential"],
    responses={404: {"description": "Not found"}},
)


@credential_router.get(
    "/schemas",
    response_model=ListCredentialSchemasResponse,
    summary="List JSON schemas for all credential types",
)
async def list_credential_schemas() -> ListCredentialSchemasResponse:
    """Return JSON schemas for all registered credential types.

    Used by the frontend to render credential creation forms dynamically.
    """

    return ListCredentialSchemasResponse(
        schemas=CredentialFactory.list_schemas(),
    )


@credential_router.get(
    "/",
    response_model=ListCredentialsResponse,
    summary="List all credentials",
)
async def list_credentials(
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> ListCredentialsResponse:
    """Return all credential records visible to the authenticated user.

    Includes the caller's own credentials plus any credentials shared to
    them through :class:`ResourceAccessPolicyBase`. Shared entries have
    their secret ``data`` payload masked — only the discriminator and
    display name survive in the response — while runtime resolution
    (e.g. the chat model service) still sees the full payload.

    Args:
        user_id (`str`):
            Injected authenticated user ID.
        access (`ResourceAccessService`):
            Injected resource access service.

    Returns:
        `ListCredentialsResponse`:
            All visible credentials paired with editability and
            (for shared entries) redacted data.
    """
    entries = await access.list_resource(user_id, ResourceKind.CREDENTIAL)
    return ListCredentialsResponse(
        credentials=entries,
        total=len(entries),
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
        user_id,
        CredentialFactory.from_dict(body.data),
    )
    return CreateCredentialResponse(credential_id=credential_id)


@credential_router.patch(
    "/{credential_id}",
    response_model=CredentialView,
    summary="Update a credential",
)
async def update_credential(
    credential_id: str,
    body: UpdateCredentialRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> CredentialView:
    """Replace the payload of an existing credential.

    Args:
        credential_id (`str`): The credential to update.
        body (`UpdateCredentialRequest`): New credential payload.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.
        access (`ResourceAccessService`): Injected access service — used
            to resolve the owning user and enforce the edit permission
            when a shared editor updates the credential.

    Returns:
        `CredentialView`: The updated credential record.

    Raises:
        `HTTPException`: 404 if the credential is not visible to the
            caller; 403 if visible but only readable.
    """
    owner_id, _ = await access.resolve_for_edit(
        user_id,
        ResourceKind.CREDENTIAL,
        credential_id,
    )

    credential = CredentialFactory.from_dict(body.data)
    credential.id = credential_id
    await storage.upsert_credential(owner_id, credential)
    # ``resolve_for_edit`` proved the record existed under ``owner_id``
    # and the upsert above just wrote back to the same key, so the read
    # is a value refresh, not an existence check. If it still comes back
    # empty (e.g. a concurrent delete), surface an explicit server error
    # rather than relying on ``assert`` (which ``-O`` strips).
    updated = await storage.get_credential(owner_id, credential_id)
    if updated is None:
        raise RuntimeError(
            f"Credential {credential_id!r} for owner {owner_id!r} "
            "disappeared immediately after a successful upsert.",
        )
    # Only reachable via ``resolve_for_edit``, so the caller has edit
    # permission by construction.
    return CredentialView.model_validate(
        {**updated.model_dump(), "editable": True},
    )


@credential_router.delete(
    "/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a credential",
)
async def delete_credential(
    credential_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> None:
    """Permanently delete a credential.

    Args:
        credential_id (`str`): The credential to delete.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.
        access (`ResourceAccessService`): Injected access service — used
            to resolve the owning user and enforce the edit permission
            when a shared editor deletes the credential.

    Raises:
        `HTTPException`: 404 if the credential is not visible to the
            caller; 403 if visible but only readable.
    """
    owner_id, _ = await access.resolve_for_edit(
        user_id,
        ResourceKind.CREDENTIAL,
        credential_id,
    )
    await storage.delete_credential(owner_id, credential_id)


@credential_router.get(
    "/{credential_id}/remote-models",
    response_model=ListRemoteModelsResponse,
    summary="List the models the credential's endpoint actually serves",
)
async def list_remote_models(
    credential_id: str,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> ListRemoteModelsResponse:
    """Query the endpoint behind a credential for its live model list.

    The static model cards surfaced by ``GET /model`` describe curated
    candidates per credential type; this endpoint asks the configured
    provider endpoint what it actually serves, so the frontend can flag
    catalog entries the endpoint does not support (and vice versa).

    Args:
        credential_id (`str`): The credential to probe.
        user_id (`str`): Injected authenticated user ID.
        access (`ResourceAccessService`): Injected access service —
            resolves the raw (unmasked) credential payload, the same
            trust level the chat runtime uses.

    Returns:
        `ListRemoteModelsResponse`: Sorted model IDs and their count.

    Raises:
        `HTTPException`: 404 if the credential is not visible to the
            caller; 400 if the credential type has no remote listing
            support; 502 if the remote endpoint fails or errors.
    """
    record = await access.resolve_credential(user_id, credential_id)
    credential = CredentialFactory.from_dict(record.data)
    try:
        models = await credential.list_remote_models()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Failed to list models from the endpoint configured on "
                f"credential {credential_id!r}: {exc}"
            ),
        ) from exc
    if models is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Credential type {credential.type!r} does not support "
                "remote model listing."
            ),
        )
    return ListRemoteModelsResponse(models=models, total=len(models))
