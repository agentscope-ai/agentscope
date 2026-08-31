# -*- coding: utf-8 -*-
"""The embedding model router."""

from fastapi import APIRouter, Depends, HTTPException, status

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
) -> ListEmbeddingModelsResponse:
    """Return all candidate embedding models under the credential type.

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

    embedding_cls = credential_cls.get_embedding_model_class()
    # Providers without embedding support report an empty catalogue
    # rather than 404 — "none available" is a valid answer here.
    models = [] if embedding_cls is None else embedding_cls.list_models()
    return ListEmbeddingModelsResponse(models=models, total=len(models))
