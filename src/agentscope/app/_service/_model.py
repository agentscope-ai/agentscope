# -*- coding: utf-8 -*-
"""Model service: builds a ChatModelBase from stored credential + config."""
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
    model_cls = credential.get_chat_model_class()
    # Resolve the built-in card once. Besides its frontend-facing input
    # types, the card is the authoritative source for the model's context
    # window used by Agent context compression. Custom models have no card,
    # so retain their constructor defaults.
    card = None
    try:
        for card in model_cls.list_models():
            if card.name == config.model:
                break
        else:
            card = None
    except Exception:  # pylint: disable=broad-except
        logger.debug(
            "Failed to look up model card for %s, using model defaults.",
            config.model,
        )

    parameters = (
        model_cls.Parameters(**config.parameters)
        if config.parameters
        else None
    )
    kwargs: dict = {
        "credential": credential,
        "model": config.model,
        "parameters": parameters,
    }
    if card is not None:
        kwargs["context_size"] = card.context_size
    model = model_cls(**kwargs)

    if card is not None:
        model.formatter.input_types = card.input_types

    return model
