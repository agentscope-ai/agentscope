"""The model router."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from fastapi.routing import APIRoute
from starlette.datastructures import QueryParams
from starlette.routing import Match
from starlette.types import Scope

from ...credential import CredentialFactory, OpenAICredential
from .._service._access import ResourceAccessService
from .._service._model_catalog import list_local_models
from ..deps import get_current_user_id, get_resource_access_service
from ._schema import ListModelsRequest, ListModelsResponse

model_router = APIRouter(
    prefix="/model",
    tags=["model"],
    responses={404: {"description": "Not found"}},
)


class _CredentialCatalogRoute(APIRoute):
    """Dispatch the credential-specific form of GET /model/ through auth."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child = super().matches(scope)
        if match == Match.FULL and not QueryParams(scope["query_string"]).get("credential_id"):
            return Match.NONE, {}
        return match, child


def _provider(provider: str):
    credential_cls = CredentialFactory.get_credential_class(provider)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{provider}' not found.",
        )
    return credential_cls


async def list_credential_models(
    provider: str,
    credential_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    access: Annotated[ResourceAccessService, Depends(get_resource_access_service)],
) -> ListModelsResponse:
    credential_cls = _provider(provider)
    record = await access.resolve_credential(user_id, credential_id)
    credential = CredentialFactory.from_dict(record.data)
    if credential.type != provider:
        raise HTTPException(400, detail="Credential provider mismatch.")
    models = None
    if isinstance(credential, OpenAICredential):
        models = await list_local_models(credential)
    if models is None:
        models = credential_cls.list_models()
    return ListModelsResponse(models=models, total=len(models))


model_router.add_api_route(
    "/",
    list_credential_models,
    methods=["GET"],
    route_class_override=_CredentialCatalogRoute,
    include_in_schema=False,
)


@model_router.get("/", response_model=ListModelsResponse)
async def list_models(body: Annotated[ListModelsRequest, Depends()]) -> ListModelsResponse:
    """List public provider models, or use authenticated access with credential_id."""
    models = _provider(body.provider).list_models()
    return ListModelsResponse(models=models, total=len(models))
