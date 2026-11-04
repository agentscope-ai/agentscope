# -*- coding: utf-8 -*-
"""Embedding model service: builds an EmbeddingModelBase from stored
credential + config.

Mirrors :mod:`._model` (which does the same for chat models).

Two entry points are provided:

- :func:`get_embedding_model` — HTTP / ChatService path. Resolves the
  credential through :class:`ResourceAccessService`, so shared
  credentials work.
- :func:`build_embedding_model` — pure record-in, model-out helper.
  Used by owner-internal paths (e.g. the knowledge-base manager, which
  already holds the KB owner's :class:`CredentialRecord`) and by
  :func:`get_embedding_model` under the hood.
"""
from fastapi import HTTPException, status

from ._access import ResourceAccessService
from ..storage import CredentialRecord, EmbeddingModelConfig
from ...credential import CredentialFactory
from ...embedding import EmbeddingModelBase


def build_embedding_model(
    credential_record: CredentialRecord,
    config: EmbeddingModelConfig,
) -> EmbeddingModelBase:
    """Construct an embedding model from an already-resolved credential.

    This is the record-in variant used by owner-internal paths (e.g.
    the KB manager, which reads the credential directly from storage
    since KB owner == credential owner). HTTP / cross-owner paths
    should call :func:`get_embedding_model` instead.

    Args:
        credential_record (`CredentialRecord`):
            The credential record whose ``data`` will be handed to the
            provider client.
        config (`EmbeddingModelConfig`):
            The embedding model configuration (``type``, ``model``,
            ``parameters``, ``dimensions``).

    Returns:
        `EmbeddingModelBase`:
            A configured embedding model instance.

    Raises:
        `HTTPException`:
            400 if the provider does not support embedding.
    """
    credential = CredentialFactory.from_dict(credential_record.data)

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

    context_size: int | None = None
    for card in embedding_cls.list_models():
        if card.name == config.model:
            context_size = card.context_size
            break

    parameters = (
        embedding_cls.Parameters(**config.parameters)
        if config.parameters
        else None
    )

    kwargs: dict = {
        "credential": credential,
        "model": config.model,
        "dimensions": config.dimensions,
        "parameters": parameters,
    }
    if context_size is not None:
        kwargs["context_size"] = context_size

    return embedding_cls(**kwargs)


async def get_embedding_model(
    user_id: str,
    config: EmbeddingModelConfig,
    access: ResourceAccessService,
) -> EmbeddingModelBase:
    """Resolve a credential through :class:`ResourceAccessService` and
    build the corresponding embedding model.

    Args:
        user_id (`str`):
            The viewer's user id (may differ from the credential owner
            for shared credentials).
        config (`EmbeddingModelConfig`):
            The embedding model configuration.
        access (`ResourceAccessService`):
            Injected resource access service.

    Returns:
        `EmbeddingModelBase`:
            A configured embedding model instance.

    Raises:
        `HTTPException`:
            404 if the credential is neither owned by ``user_id`` nor
            shared to them.
            400 if the provider does not support embedding.
    """
    credential_record = await access.resolve_credential(
        user_id,
        config.credential_id,
    )
    return build_embedding_model(credential_record, config)
