# -*- coding: utf-8 -*-
"""The utils for storage."""

from pydantic import BaseModel, SecretStr


def _dump_with_secrets(model: BaseModel) -> dict:
    """Dump the BaseModel instance with SecretStr fields. Used for
    storage.

    Args:
        model (`BaseModel`):
            The model instance to dump.

    Returns:
        `dict`:
            The dumped JSON with secrets included.
    """
    # Use mode='json' so that Pydantic converts non-JSON-native types
    # (e.g. datetime, UUID) to their JSON-compatible representations.
    # SecretStr fields will be masked at this step.
    result = model.model_dump(mode="json")

    for field_name, _ in model.__class__.model_fields.items():
        value = getattr(model, field_name)
        if isinstance(value, SecretStr):
            result[field_name] = value.get_secret_value()

    return result
