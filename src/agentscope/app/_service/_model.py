# -*- coding: utf-8 -*-
"""Model service: builds a ChatModelBase from stored credential + config."""
from ._access import ResourceAccessService
from ..storage import ChatModelConfig, RealtimeModelConfig
from ...credential import CredentialFactory
from ...model import ChatModelBase
from ...realtime import RealtimeModelBase
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

    Matching model-card defaults fill omitted parameters. Explicitly
    configured values, including ``None``, take precedence.

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
        `ValueError`:
            If the credential does not support the explicit model class.
    """
    credential_record = await access.resolve_credential(
        user_id,
        config.credential_id,
    )

    credential = CredentialFactory.from_dict(credential_record.data)
    if config.model_class:
        for model_cls in credential.get_chat_model_classes():
            if getattr(model_cls, "type", "") == config.model_class:
                break
        else:
            raise ValueError(
                f"Chat model class {config.model_class!r} is not available "
                f"for credential {config.credential_id!r}.",
            )
    else:
        model_cls = credential.get_chat_model_class()

    card = None
    try:
        card = next(
            (
                candidate
                for candidate in model_cls.list_models()
                if candidate.name == config.model
            ),
            None,
        )
    except Exception:  # pylint: disable=broad-except
        logger.debug(
            "Failed to look up model card for %s, using defaults.",
            config.model,
        )

    card_defaults = (
        {
            name: override["default"]
            for name, override in card.parameters_overrides.items()
            if isinstance(override, dict) and "default" in override
        }
        if card is not None
        else {}
    )
    merged_parameters = {**card_defaults, **config.parameters}
    parameters = (
        model_cls.Parameters(**merged_parameters)
        if merged_parameters
        else None
    )
    model = model_cls(
        credential=credential,
        model=config.model,
        parameters=parameters,
    )

    # Override the context size and the formatter's input types with the
    # built-in model card's when one matches. Keep metadata application
    # best-effort for custom models that do not expose a formatter.
    if card is not None:
        try:
            model.context_size = card.context_size
            model.formatter.input_types = card.input_types
        except Exception:  # pylint: disable=broad-except
            logger.debug(
                "Failed to apply model card metadata for %s.",
                config.model,
            )

    return model


async def get_realtime_model(
    user_id: str,
    config: RealtimeModelConfig,
    access: ResourceAccessService,
) -> RealtimeModelBase:
    """Build a realtime model from a stored credential and config.

    Args:
        user_id (`str`): The viewer's user id.
        config (`RealtimeModelConfig`): The realtime model configuration.
        access (`ResourceAccessService`): Injected access service.

    Returns:
        `RealtimeModelBase`: The matching realtime model instance.

    Raises:
        `ValueError`: If the credential does not expose the configured
            realtime adapter or model.
    """
    credential_record = await access.resolve_credential(
        user_id,
        config.credential_id,
    )
    credential = CredentialFactory.from_dict(credential_record.data)

    for model_cls in credential.get_realtime_model_classes():
        if model_cls.type != config.type:
            continue
        card = next(
            (
                candidate
                for candidate in model_cls.list_models()
                if candidate.name == config.model
            ),
            None,
        )
        if card is None:
            continue
        parameters = (
            model_cls.Parameters(**config.parameters)
            if config.parameters
            else None
        )
        return model_cls(
            credential=credential,
            model=config.model,
            parameters=parameters,
            model_card=card,
        )

    raise ValueError(
        f"Realtime model {config.model!r} with type {config.type!r} "
        f"is not available for credential {config.credential_id!r}.",
    )
