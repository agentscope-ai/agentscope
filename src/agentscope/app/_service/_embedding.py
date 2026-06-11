# -*- coding: utf-8 -*-
"""Embedding model service: builds an EmbeddingModelBase from stored
credential + config.

Mirrors :mod:`._model` (which does the same for chat models).
"""
from fastapi import HTTPException, status

from ..storage import StorageBase, EmbeddingModelConfig
from ...credential import CredentialFactory
from ...embedding import EmbeddingModelBase


async def get_embedding_model(
    user_id: str,
    config: EmbeddingModelConfig,
    storage: StorageBase,
) -> EmbeddingModelBase:
    """Construct an embedding model from a stored credential and config.

    This is the embedding counterpart of
    :func:`~agentscope.app._service._model.get_model`.  It loads the
    user's credential from storage, resolves the matching embedding
    model class, looks up the model card for ``context_size``, and
    constructs a ready-to-use instance.

    Args:
        user_id (`str`):
            The authenticated user id (credential owner).
        config (`EmbeddingModelConfig`):
            The embedding model configuration containing
            ``type``, ``credential_id``, ``model``, and
            ``parameters``.
        storage (`StorageBase`):
            The storage backend for loading credentials.

    Returns:
        `EmbeddingModelBase`:
            A configured embedding model instance.

    Raises:
        `HTTPException`:
            404 if the credential is not found.
            400 if the provider does not support embedding.
    """
    # 1. Load credential from storage.
    credential_record = await storage.get_credential(
        user_id,
        config.credential_id,
    )
    if credential_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credential {config.credential_id!r} not found.",
        )

    credential = CredentialFactory.from_dict(credential_record.data)

    # 2. Resolve the embedding model class from the credential type.
    credential_cls = CredentialFactory.get_credential_class(config.type)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {config.type!r} not found.",
        )

    embedding_cls = credential_cls.get_embedding_model_class()
    if embedding_cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Provider {config.type!r} does not support "
                f"embedding models."
            ),
        )

    # 3. Look up the model card for context_size.
    context_size: int | None = None
    for card in embedding_cls.list_models():
        if card.name == config.model:
            context_size = card.context_size
            break

    # 4. Build parameters.
    parameters = (
        embedding_cls.Parameters(**config.parameters)
        if config.parameters
        else None
    )

    # 5. Construct the model, passing context_size from the card.
    kwargs: dict = {
        "credential": credential,
        "model": config.model,
        "parameters": parameters,
    }
    if context_size is not None:
        kwargs["context_size"] = context_size

    return embedding_cls(**kwargs)
