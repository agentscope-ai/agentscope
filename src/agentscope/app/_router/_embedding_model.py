# -*- coding: utf-8 -*-
"""The embedding model router."""

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_model_card_dirs
from ._schema import ListEmbeddingModelsResponse, ListEmbeddingModelsRequest
from ...credential import CredentialFactory

embedding_model_router = APIRouter(
    prefix="/embedding-model",
    tags=["embedding-model"],
    responses={404: {"description": "Not found"}},
)


@embedding_model_router.get(
    "/",
    response_model=ListEmbeddingModelsResponse,
    summary=(
        "List all candidate embedding models under the given credential type"
    ),
)
async def list_embedding_models(
    body: ListEmbeddingModelsRequest = Depends(),
    model_card_dirs: dict = Depends(get_model_card_dirs),
) -> ListEmbeddingModelsResponse:
    """Return all candidate embedding models under the credential type.

    Includes the built-in YAML cards plus any cards configured through
    ``create_app(model_card_dirs=...)`` for this credential type.

    Unlike ``/knowledge_bases/embedding_models``, which narrows the
    list to what the knowledge base's dimension policy accepts, this
    endpoint reports the provider's full catalogue — it answers "what
    can this credential do", not "what can I build a KB with".

    Args:
        body (ListEmbeddingModelsRequest): The request body.

    Returns:
        `ListEmbeddingModelsResponse`: The response body.
    """
    credential_cls = CredentialFactory.get_credential_class(body.provider)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{body.provider}' not found.",
        )

    from .._service import card_subdirs

    embedding_cls = credential_cls.get_embedding_model_class()
    # Providers without embedding support report an empty catalogue
    # rather than 404 — "none available" is a valid answer here.
    extra_dirs = (
        None
        if embedding_cls is None
        else card_subdirs(model_card_dirs, body.provider).get("embedding")
    )
    models = (
        [] if embedding_cls is None else embedding_cls.list_models(
            extra_yaml_dirs=extra_dirs,
        )
    )
    return ListEmbeddingModelsResponse(models=models, total=len(models))
