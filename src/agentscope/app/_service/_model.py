# -*- coding: utf-8 -*-
"""Model service: builds a ChatModelBase from stored credential + config."""
from pathlib import Path
from typing import Mapping

from ._access import ResourceAccessService
from ..storage import ChatModelConfig
from ...credential import CredentialFactory
from ...model import ChatModelBase
from ..._logging import logger

_CARD_KINDS = ("chat", "embedding", "tts")


def card_subdirs(
    model_card_dirs: Mapping[str, Path] | None,
    credential_type: str,
) -> dict[str, list[str]]:
    """Resolve the extra card directories configured for a credential type.

    ``model_card_dirs`` maps a credential type (e.g.
    ``"dashscope_credential"``) to a base directory whose optional
    ``chat/`` / ``embedding/`` / ``tts/`` subdirectories hold extra YAML
    model cards.

    Args:
        model_card_dirs (`Mapping[str, Path] | None`):
            The mapping installed on ``app.state`` by ``create_app``.
        credential_type (`str`):
            The credential type literal (e.g. ``"openai_credential"``).

    Returns:
        `dict[str, list[str]]`:
            Kind name (``chat`` / ``embedding`` / ``tts``) to the list of
            existing directory paths. Kinds without a configured or
            existing subdirectory are omitted.
    """
    base = (model_card_dirs or {}).get(credential_type)
    if base is None:
        return {}
    out: dict[str, list[str]] = {}
    for kind in _CARD_KINDS:
        sub = Path(base) / kind
        if sub.is_dir():
            out.setdefault(kind, []).append(str(sub))
    return out


async def get_model(
    user_id: str,
    config: ChatModelConfig,
    access: ResourceAccessService,
    model_card_dirs: Mapping[str, Path] | None = None,
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
        model_card_dirs (`Mapping[str, Path] | None`):
            Extra model-card directories keyed by credential type (see
            :func:`card_subdirs`). Cards found there refine the built-in
            catalog used for the context-size / input-types lookup.

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

    # Override the context size and the formatter's input types with the
    # model card's when one matches — built-in or from the configured
    # extra card directories. Custom models with no card anywhere keep
    # the defaults.
    try:
        extra_dirs = card_subdirs(model_card_dirs, credential.type).get("chat")
        for card in model_cls.list_models(extra_yaml_dirs=extra_dirs):
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
