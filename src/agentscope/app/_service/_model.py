# -*- coding: utf-8 -*-
"""Model service: builds a ChatModelBase from stored credential + config."""
from typing import Type

from fastapi import HTTPException, status

from ..storage import StorageBase, ChatModelConfig
from ...credential import CredentialFactory
from ...model import ChatModelBase


async def get_model(
    user_id: str,
    config: ChatModelConfig,
    storage: StorageBase,
) -> ChatModelBase:
    """Get the model instance from the configuration and storage.

    Args:
        user_id (`str`):
            The user id.
        config (`ChatModelConfig`):
            The chat model configuration.
        storage (`StorageBase`):
            The storage instance.

    Returns:
        `ChatModelBase`:
            The model instance.
    """
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
    classes = credential.get_chat_model_classes()
    model_cls = _resolve_chat_class(classes, config.model, config.model_class)
    parameters = (
        model_cls.Parameters(**config.parameters)
        if config.parameters
        else None
    )
    return model_cls(
        credential=credential,
        model=config.model,
        parameters=parameters,
    )


def _resolve_chat_class(
    classes: list[Type[ChatModelBase]],
    model: str,
    model_class: str = "",
) -> Type[ChatModelBase]:
    """Pick the chat model class that should handle this request.

    Resolution order:
    1. If ``model_class`` is provided, match by the class ``type`` attribute.
    2. Otherwise, find the class whose model cards list the given model name.
    3. Fall back to the first class in the list.
    """
    if model_class:
        for cls in classes:
            if getattr(cls, "type", "") == model_class:
                return cls

    for cls in classes:
        if any(card.name == model for card in cls.list_models()):
            return cls

    return classes[0]
