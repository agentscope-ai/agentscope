# -*- coding: utf-8 -*-
"""Model service: builds a ChatModelBase from stored credential + config."""
from typing import Type

from ._access import ResourceAccessService
from ..storage import ChatModelConfig
from ...credential import CredentialFactory
from ...model import ChatModelBase
from ..._logging import logger


async def get_model(
    user_id: str,
    config: ChatModelConfig,
    access: ResourceAccessService,
) -> ChatModelBase:
    """Build a chat model instance from a stored credential and config.

    Credentials are resolved through :class:`ResourceAccessService` so
    both the viewer's own credentials and any shared to them via the
    resource access policy work. Runtime paths use
    :meth:`ResourceAccessService.resolve_credential` which returns the
    raw record (not the masked view) — required for making real
    provider calls.

    Args:
        user_id (`str`):
            The viewer's user id. May differ from the credential owner
            when the credential is shared.
        config (`ChatModelConfig`):
            The chat model configuration.
        access (`ResourceAccessService`):
            Injected resource access service.

    Returns:
        `ChatModelBase`:
            The model instance.

    Raises:
        `HTTPException`:
            404 when the credential is neither owned by ``user_id`` nor
            shared to them.
    """
    credential_record = await access.resolve_credential(
        user_id,
        config.credential_id,
    )

    credential = CredentialFactory.from_dict(credential_record.data)
    classes = credential.get_chat_model_classes()
    model_cls = _resolve_chat_class(classes, config.model, config.model_class)
    parameters = (
        model_cls.Parameters(**config.parameters)
        if config.parameters
        else None
    )
    model = model_cls(
        credential=credential,
        model=config.model,
        parameters=parameters,
    )

    # Override the formatter's input types with the built-in model card's
    # when one matches; custom models have no card, so keep the default.
    try:
        for card in model_cls.list_models():
            if card.name == config.model:
                model.formatter.input_types = card.input_types
                break
    except Exception:  # pylint: disable=broad-except
        logger.debug(
            "Failed to look up model card for %s, using formatter defaults.",
            config.model,
        )

    return model


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
