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


def _get_literal_values(credential_cls: Type[CredentialBase]) -> set[str]:
    """Return the allowed ``type`` discriminator values for a credential class.

    Reads the ``Literal`` annotation on the ``type`` field.  Returns an empty
    set when the class does not declare a ``type`` field at all.

    Args:
        credential_cls (`Type[CredentialBase]`):
            The credential subclass to inspect.

    Returns:
        `set[str]`:
            The set of values permitted by the ``type`` ``Literal`` annotation.
    """
    type_hint = get_type_hints(credential_cls).get("type")
    if type_hint is None:
        return set()
    return set(get_args(type_hint))


def _get_discriminator_value(
    credential_cls: Type[CredentialBase],
) -> str | None:
    """Return the resolved ``type`` discriminator value for a credential class.

    The discriminator is the field's default value, i.e. the value written into
    serialized records and the one Pydantic tags the union with.  Returns
    ``None`` when the class has no ``type`` field.

    Args:
        credential_cls (`Type[CredentialBase]`):
            The credential subclass to inspect.

    Returns:
        `str | None`:
            The default ``type`` value, or ``None`` if there is no ``type``
            field.
    """
    field = credential_cls.model_fields.get("type")
    if field is None:
        return None
    return field.default


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
        default so Pydantic can use it as a discriminator.  Registration is
        validated eagerly so that misconfigured credentials fail here, at the
        call site, rather than producing an opaque ``ValidationError`` from
        :meth:`from_dict` later (see issue #1961).

        Args:
            credential_cls (`Type[CredentialBase]`):
                The subclass to register.

        Raises:
            `ValueError`:
                If the class has no ``type`` discriminator field, if its
                ``type`` default value is not contained in its own ``Literal``
                annotation, or if its discriminator value is already used by
                a built-in or previously-registered credential class.
        """
        if credential_cls in cls._classes:
            return

        cls._validate_discriminator(credential_cls)

        cls._classes.append(credential_cls)
        cls._adapter = None  # invalidate so it's rebuilt on next use

    @classmethod
    def _validate_discriminator(
        cls,
        credential_cls: Type[CredentialBase],
    ) -> None:
        """Validate the ``type`` discriminator of a credential class.

        Ensures the class declares a ``type`` field whose default value is one
        of its permitted ``Literal`` values, and that the value does not
        collide with any already-registered credential.  Raises ``ValueError``
        with an actionable message otherwise.

        Args:
            credential_cls (`Type[CredentialBase]`):
                The candidate class about to be registered.
        """
        allowed = _get_literal_values(credential_cls)
        default = _get_discriminator_value(credential_cls)

        if default is None:
            raise ValueError(
                f"{credential_cls.__name__} cannot be registered: it must "
                "declare a `type` field with a Literal default to act as a "
                "discriminator.",
            )

        if allowed and default not in allowed:
            raise ValueError(
                f"{credential_cls.__name__} cannot be registered: its `type` "
                f"default {default!r} is not among the permitted Literal "
                f"values {sorted(allowed)!r}. The default value must appear "
                "in the Literal annotation, e.g. "
                "`type: Literal['<value>'] = '<value>'`.",
            )

        for registered in cls._classes:
            if _get_discriminator_value(registered) == default:
                raise ValueError(
                    f"{credential_cls.__name__} cannot be registered: its "
                    f"`type` discriminator {default!r} is already used by "
                    f"{registered.__name__}. Each credential must declare a "
                    "unique discriminator value.",
                )

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
            allowed = _get_literal_values(c)
            if not allowed:
                continue
            # Match against the first declared Literal value to preserve the
            # historical lookup semantics.
            if next(iter(allowed)) == provider:
                return c
        return None

    @classmethod
    def list_schemas(cls) -> list[dict]:
        """Return JSON schemas for all registered credential types.

        Used by the frontend to render credential forms dynamically.
        """
        return [c.model_json_schema() for c in cls._classes]
