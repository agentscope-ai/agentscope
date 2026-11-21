"""The model router."""

from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status

from ...credential import CredentialFactory, OpenAICredential
from ...model import ModelCard, OpenAIChatModel
from .._service._access import ResourceAccessService
from ..deps import get_current_user_id, get_resource_access_service
from ._schema import ListModelsRequest, ListModelsResponse

model_router = APIRouter(
    prefix="/model",
    tags=["model"],
    responses={404: {"description": "Not found"}},
)


async def _list_local_models(
    credential: OpenAICredential,
) -> list[ModelCard] | None:
    """Use the server's own chat catalog for loopback OpenAI-compatible URLs."""
    if not credential.base_url:
        return None
    address = urlsplit(credential.base_url)
    if address.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return None
    if (
        address.scheme not in {"http", "https"}
        or address.username is not None
        or address.password is not None
        or address.query
        or address.fragment
    ):
        raise HTTPException(
            status_code=400, detail="Invalid local model server URL"
        )

    try:
        async with httpx.AsyncClient(
            timeout=5,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(
                credential.base_url.rstrip("/") + "/models",
                headers={
                    "Authorization": f"Bearer {credential.api_key.get_secret_value()}",
                },
            )
            response.raise_for_status()
            entries = response.json()["data"]
        if not isinstance(entries, list):
            raise TypeError("Expected a model list")
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not read models from the local model server.",
        ) from exc

    schema = OpenAIChatModel.Parameters.model_json_schema()
    schema["properties"].pop("voice", None)
    chat_entries = {
        entry["id"]: entry
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("id"), str)
        and entry["id"]
        and entry.get("model_type") in (None, "llm", "vlm")
    }
    cards = []
    for name, entry in chat_entries.items():
        display_name = entry.get("display_name")
        target = (
            chat_entries.get(display_name)
            if isinstance(display_name, str)
            else None
        )
        # oMLX exposes Anthropic compatibility names for the same local weights.
        # Keep the alias selectable if its underlying model has no catalog entry.
        if (
            entry.get("owned_by") == "omlx"
            and entry.get("anthropic_family_tier")
            and target is not None
            and target.get("owned_by") == "omlx"
            and not target.get("anthropic_family_tier")
            and display_name != name
        ):
            continue
        size = entry.get("max_model_len")
        context = size if type(size) is int and size > 0 else 8192
        limit = entry.get("max_tokens")
        output = min(
            context, limit if type(limit) is int and limit > 0 else 4096
        )
        label = (
            f"{display_name} ({name})"
            if isinstance(display_name, str)
            and display_name
            and display_name != name
            else name
        )
        cards.append(
            ModelCard(
                name=name,
                label=label,
                model_class=OpenAIChatModel.type,
                status="active",
                context_size=context,
                output_size=output,
                parameter_schema=schema,
                parameters_overrides={},
            )
        )
    return cards


@model_router.get(
    "/",
    response_model=ListModelsResponse,
    summary="List all candidate models under the given credential type",
)
async def list_models(
    body: Annotated[ListModelsRequest, Depends()],
    access: Annotated[
        ResourceAccessService, Depends(get_resource_access_service)
    ],
    x_user_id: Annotated[str | None, Header()] = None,
) -> ListModelsResponse:
    """Return all candidate models under the given credential type.

    Args:
        body (ListModelsRequest): The request body.

    Returns:
        `ListModelsResponse`: The response body.
    """
    credential_cls = CredentialFactory.get_credential_class(body.provider)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{body.provider}' not found.",
        )

    if body.credential_id:
        user_id = await get_current_user_id(x_user_id or "")
        record = await access.resolve_credential(user_id, body.credential_id)
        credential = CredentialFactory.from_dict(record.data)
        if credential.type != body.provider:
            raise HTTPException(
                status_code=400, detail="Credential provider mismatch."
            )
        if isinstance(credential, OpenAICredential):
            models = await _list_local_models(credential)
            if models is not None:
                return ListModelsResponse(models=models, total=len(models))
    models = credential_cls.list_models()
    return ListModelsResponse(models=models, total=len(models))
