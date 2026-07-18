# -*- coding: utf-8 -*-
"""Model service: builds a ChatModelBase from stored credential + config."""
from typing import Type
from ._access import ResourceAccessService
from ..storage import ChatModelConfig
from ...credential import CredentialFactory
from ...model import ChatModelBase
from ..._logging import logger


def _get_model_input_types(
    model_cls: Type[ChatModelBase],
    model_name: str,
) -> list[str] | None:
    """Look up ``input_types`` from the built-in model card for *model_name*.

    Returns ``None`` when no matching card is found (e.g. custom models)
    so callers can fall back to the formatter's default.
    """
    try:
        for card in model_cls.list_models():
            if card.name == model_name:
                return card.input_types
    except Exception:
        logger.debug(
            "Failed to look up model card for %s, "
            "using formatter defaults.",
            model_name,
        )
    return None


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
    model_cls = credential.get_chat_model_class()
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

    input_types = _get_model_input_types(model_cls, config.model)
    if input_types is not None and hasattr(model, "formatter"):
        model.formatter.input_types = input_types

    return model
