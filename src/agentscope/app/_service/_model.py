# -*- coding: utf-8 -*-
"""Model service: builds a ChatModelBase from stored credential + config."""
from fastapi import HTTPException, status

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
            shared to them; 400 when the stored credential belongs to a
            provider that serves no chat model, such as a classifier-only
            credential.
    """
    credential_record = await access.resolve_credential(
        user_id,
        config.credential_id,
    )

    credential = CredentialFactory.from_dict(credential_record.data)
    try:
        model_cls = credential.get_chat_model_class()
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Credential {config.credential_id!r} is a "
                f"{type(credential).__name__} and serves no chat model."
            ),
        ) from e
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

    # Override the context size and the formatter's input types with the
    # built-in model card's when one matches; custom models have no card,
    # so keep the defaults.
    try:
        for card in model_cls.list_models():
            if card.name == config.model:
                model.context_size = card.context_size
                model.formatter.input_types = card.input_types
                break
    except Exception:  # pylint: disable=broad-except
        logger.debug(
            "Failed to look up model card for %s, using defaults.",
            config.model,
        )

    return model
