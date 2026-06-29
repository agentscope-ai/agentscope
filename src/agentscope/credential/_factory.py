# -*- coding: utf-8 -*-
"""The credential factory class."""
from typing import Annotated, Type, Union, get_args, get_type_hints

from pydantic import TypeAdapter, Field

from ._anthropic import AnthropicCredential
from ._dashscope import DashScopeCredential
from ._deepseek import DeepSeekCredential
from ._gemini import GeminiCredential
from ._moonshot import MoonshotCredential
from ._ollama import OllamaCredential
from ._openai import OpenAICredential
from ._xai import XAICredential
from ._base import CredentialBase


class CredentialFactory:
    """Registry and deserializer for :class:`CredentialBase` subclasses.

    Built-in credential types are pre-registered.  Call
    :meth:`register_credential` to add custom types before starting the app.

    Usage::

        # Deserialize from storage
        credential = CredentialFactory.from_dict(record.data)

        # Register a custom type
        CredentialFactory.register_credential(MyCredential)

        # List schemas for the frontend form
        schemas = CredentialFactory.list_schemas()
    """

    _classes: list[Type[CredentialBase]] = [
        AnthropicCredential,
        DashScopeCredential,
        DeepSeekCredential,
        GeminiCredential,
        MoonshotCredential,
        OllamaCredential,
        OpenAICredential,
        XAICredential,
    ]
    _adapter: TypeAdapter[CredentialBase] | None = None

    @staticmethod
    def _get_discriminator_values(
        credential_cls: Type[CredentialBase],
    ) -> tuple[str, ...]:
        """Return the ``type`` literal values declared by a credential.

        Args:
            credential_cls: The credential class to inspect.

        Returns:
            The string discriminator values declared in the ``type`` field.

        Raises:
            ValueError: If the class does not declare a valid literal
                ``type`` field.
        """
        hints = get_type_hints(credential_cls)
        type_hint = hints.get("type")
        values = get_args(type_hint) if type_hint is not None else ()
        if not values or not all(isinstance(value, str) for value in values):
            raise ValueError(
                f"{credential_cls.__name__} must define a `type` field "
                "annotated as `Literal['your_credential_type']`.",
            )
        return values

    @classmethod
    def _get_adapter(cls) -> TypeAdapter[CredentialBase]:
        if cls._adapter is None:
            union = Annotated[  # type: ignore[valid-type]
                Union[tuple(cls._classes)],
                Field(discriminator="type"),
            ]
            cls._adapter = TypeAdapter(union)
        return cls._adapter

    @classmethod
    def register_credential(cls, credential_cls: Type[CredentialBase]) -> None:
        """Register a custom :class:`CredentialBase` subclass.

        The class must define a ``type`` field with a unique ``Literal``
        default so Pydantic can use it as a discriminator.

        Args:
            credential_cls: The subclass to register.
        """
        if not issubclass(credential_cls, CredentialBase):
            raise TypeError(
                "credential_cls must be a CredentialBase subclass.",
            )

        values = cls._get_discriminator_values(credential_cls)
        type_field = credential_cls.model_fields.get("type")
        if type_field is None:
            raise ValueError(
                f"{credential_cls.__name__} must define a `type` field.",
            )

        default = type_field.default
        if default not in values:
            expected = ", ".join(repr(value) for value in values)
            raise ValueError(
                f"{credential_cls.__name__}.type default must match its "
                f"Literal discriminator. Expected one of {expected}, got "
                f"{default!r}.",
            )

        registered: dict[str, str] = {}
        for existing_cls in cls._classes:
            for value in cls._get_discriminator_values(existing_cls):
                registered[value] = existing_cls.__name__
        duplicates = [value for value in values if value in registered]
        if duplicates:
            details = ", ".join(
                f"{value!r} already registered by {registered[value]}"
                for value in duplicates
            )
            raise ValueError(
                f"{credential_cls.__name__} declares duplicate credential "
                f"type discriminator(s): {details}.",
            )

        cls._classes.append(credential_cls)
        cls._adapter = None  # invalidate so it's rebuilt on next use

    @classmethod
    def from_dict(cls, data: dict) -> CredentialBase:
        """Deserialize a credential dict (from storage) to a typed instance.

        Args:
            data: Raw dict containing a ``"type"`` key.

        Returns:
            A typed :class:`CredentialBase` subclass instance.
        """
        return cls._get_adapter().validate_python(data)

    @classmethod
    def get_credential_class(
        cls,
        provider: str,
    ) -> Type[CredentialBase] | None:
        """Return the credential class for the given provider type, or None.

        Args:
            provider: The ``type`` discriminator value (e.g. ``"openai"``).

        Returns:
            The matching :class:`CredentialBase` subclass, or ``None`` if not
            found.
        """
        for c in cls._classes:
            if provider in cls._get_discriminator_values(c):
                return c
        return None

    @classmethod
    def list_schemas(cls) -> list[dict]:
        """Return JSON schemas for all registered credential types.

        Used by the frontend to render credential forms dynamically.
        """
        return [c.model_json_schema() for c in cls._classes]
