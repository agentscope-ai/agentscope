# -*- coding: utf-8 -*-
"""Unit tests for :class:`CredentialFactory`.

These tests cover discriminator resolution, custom-credential registration,
and the fail-fast validation that guards against the two silent failure paths
reported in issue #1961:

- Registering a credential whose ``type`` discriminator collides with an
  already-registered class (built-in or custom).
- Registering a credential whose ``type`` field has a ``Literal`` that does
  not contain its own default value.

The factory stores its registry on the class object
(``CredentialFactory._classes`` / ``_adapter``), so every test runs through a
``restore_factory_registry`` fixture that snapshots and restores the
class-level state to keep tests deterministic and isolated.
"""
# pylint: disable=protected-access
import copy
from typing import Generator, List, Literal, Type
from unittest import TestCase

import pytest
from pydantic import Field, SecretStr

from agentscope.credential import (
    AnthropicCredential,
    CredentialBase,
    CredentialFactory,
    DashScopeCredential,
    DeepSeekCredential,
    GeminiCredential,
    MoonshotCredential,
    OllamaCredential,
    OpenAICredential,
    XAICredential,
)


class _CustomCredential(CredentialBase):
    """A valid custom credential with a unique discriminator."""

    type: Literal["custom_credential"] = "custom_credential"
    api_key: SecretStr = Field(description="The API key.")

    @classmethod
    def get_chat_model_class(cls) -> Type["CredentialBase"]:
        """Return ``None`` — only registration is exercised here."""
        raise NotImplementedError


class _CollidingCustomCredential(CredentialBase):
    """A custom credential that reuses a built-in discriminator value."""

    type: Literal["openai_credential"] = "openai_credential"
    api_key: SecretStr = Field(description="The API key.")

    @classmethod
    def get_chat_model_class(cls) -> Type["CredentialBase"]:
        """Return ``None`` — only registration is exercised here."""
        raise NotImplementedError


class _LiteralDefaultMismatchCredential(CredentialBase):
    """A credential whose ``type`` Literal excludes its own default.

    Reproduces the reporter's field
    ``type: Literal["openai_credential"] = "custom_credential"``.
    """

    type: Literal["openai_credential"] = "custom_credential"  # noqa: S105
    api_key: SecretStr = Field(default=SecretStr("k"))

    @classmethod
    def get_chat_model_class(cls) -> Type["CredentialBase"]:
        """Return ``None`` — only registration is exercised here."""
        raise NotImplementedError


class _TypelessCredential(CredentialBase):
    """A credential that forgets to declare a ``type`` discriminator."""

    api_key: SecretStr = Field(default=SecretStr("k"))

    @classmethod
    def get_chat_model_class(cls) -> Type["CredentialBase"]:
        """Return ``None`` — only registration is exercised here."""
        raise NotImplementedError


@pytest.fixture
def restore_factory_registry() -> Generator[None, None, None]:
    """Snapshot and restore the factory's class-level registry.

    ``CredentialFactory`` keeps its registered classes and cached adapter on
    the class object, so registering a custom type in one test would leak
    into every other test.  This fixture deep-copies the registry before a
    test and restores it (and invalidates the adapter) afterwards.
    """
    snapshot = {
        "classes": copy.copy(CredentialFactory._classes),
        "adapter": CredentialFactory._adapter,
    }
    yield
    CredentialFactory._classes = snapshot["classes"]
    CredentialFactory._adapter = snapshot["adapter"]


@pytest.mark.usefixtures("restore_factory_registry")
class TestCredentialFactoryRegistration(TestCase):
    """Registration, validation, and lookup behavior of the factory."""

    def test_builtin_discriminators_resolve_to_correct_class(self) -> None:
        """``from_dict`` maps each built-in ``type`` to its class."""
        expected = {
            "anthropic_credential": AnthropicCredential,
            "dashscope_credential": DashScopeCredential,
            "deepseek_credential": DeepSeekCredential,
            "gemini_credential": GeminiCredential,
            "moonshot_credential": MoonshotCredential,
            "ollama_credential": OllamaCredential,
            "openai_credential": OpenAICredential,
            "xai_credential": XAICredential,
        }
        for discriminator, expected_cls in expected.items():
            with self.subTest(discriminator=discriminator):
                instance = CredentialFactory.from_dict(
                    {"type": discriminator, "api_key": "sk-test"},
                )
                self.assertIs(type(instance), expected_cls)

    def test_register_valid_custom_credential_round_trips(self) -> None:
        """A uniquely-discriminated custom class registers and round-trips."""
        CredentialFactory.register_credential(_CustomCredential)

        deserialized = CredentialFactory.from_dict(
            {"type": "custom_credential", "api_key": "sk-test"},
        )
        self.assertIs(type(deserialized), _CustomCredential)

        round_tripped = CredentialFactory.from_dict(
            _CustomCredential(api_key="sk-test").model_dump(),
        )
        self.assertIs(type(round_tripped), _CustomCredential)

    def test_register_idempotent_for_same_class(self) -> None:
        """Registering the same class twice is a silent no-op."""
        CredentialFactory.register_credential(_CustomCredential)
        count_after_first = len(CredentialFactory._classes)
        CredentialFactory.register_credential(_CustomCredential)
        count_after_second = len(CredentialFactory._classes)

        self.assertEqual(count_after_first, count_after_second)

    def test_register_colliding_discriminator_raises(self) -> None:
        """Reusing a built-in discriminator value fails fast (#1961-A)."""
        with self.assertRaises(ValueError) as ctx:
            CredentialFactory.register_credential(_CollidingCustomCredential)

        message = str(ctx.exception)
        self.assertIn("openai_credential", message)
        self.assertIn("OpenAICredential", message)

    def test_register_literal_default_mismatch_raises(self) -> None:
        """A ``type`` whose default is outside its ``Literal`` fails (#1961-B).

        Reproduces the reporter's field
        ``type: Literal["openai_credential"] = "custom_credential"``.
        """
        with self.assertRaises(ValueError) as ctx:
            CredentialFactory.register_credential(
                _LiteralDefaultMismatchCredential,
            )

        message = str(ctx.exception)
        self.assertIn("custom_credential", message)
        self.assertIn("openai_credential", message)

    def test_register_without_type_field_raises(self) -> None:
        """A class with no ``type`` discriminator cannot be registered."""
        with self.assertRaises(ValueError):
            CredentialFactory.register_credential(_TypelessCredential)


@pytest.mark.usefixtures("restore_factory_registry")
class TestCredentialFactoryLookup(TestCase):
    """``get_credential_class`` and ``list_schemas`` behavior."""

    def test_get_credential_class_for_builtin(self) -> None:
        """``get_credential_class`` resolves built-in discriminators."""
        self.assertIs(
            CredentialFactory.get_credential_class("openai_credential"),
            OpenAICredential,
        )
        self.assertIs(
            CredentialFactory.get_credential_class("dashscope_credential"),
            DashScopeCredential,
        )

    def test_get_credential_class_unknown_returns_none(self) -> None:
        """Unknown discriminators resolve to ``None``."""
        self.assertIsNone(
            CredentialFactory.get_credential_class("does_not_exist"),
        )

    def test_get_credential_class_after_custom_register(self) -> None:
        """A registered custom class is resolvable by its discriminator."""
        CredentialFactory.register_credential(_CustomCredential)

        self.assertIs(
            CredentialFactory.get_credential_class("custom_credential"),
            _CustomCredential,
        )

    def test_list_schemas_includes_registered_custom(self) -> None:
        """``list_schemas`` exposes registered custom credentials."""
        CredentialFactory.register_credential(_CustomCredential)

        schema_titles: List[str] = [
            schema.get("title", "")
            for schema in CredentialFactory.list_schemas()
        ]
        self.assertIn("_CustomCredential", schema_titles)
