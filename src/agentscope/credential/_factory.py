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
        if credential_cls in cls._classes:
            return
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
            hints = get_type_hints(c)
            type_hint = hints.get("type")
            if type_hint is None:
                continue
            args = get_args(type_hint)

            if args and args[0] == provider:
                return c
        return None

    @classmethod
    def list_schemas(cls) -> list[dict]:
        """Return JSON schemas for all registered credential types.

        Used by the frontend to render credential forms dynamically.
        """
        return [c.model_json_schema() for c in cls._classes]
