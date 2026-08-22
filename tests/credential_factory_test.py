# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :mod:`agentscope.credential._factory`."""
from typing import Literal, Type
from unittest import TestCase

from pydantic import ConfigDict, Field, SecretStr

from agentscope.credential import CredentialBase, CredentialFactory
from agentscope.model import OpenAIChatModel


class CredentialFactoryCustomRegistrationTest(TestCase):
    """Custom credential registration behavior."""

    def setUp(self) -> None:
        """Snapshot factory state so tests do not leak registrations."""
        self._classes = list(CredentialFactory._classes)
        self._adapter = CredentialFactory._adapter

    def tearDown(self) -> None:
        """Restore the global credential registry."""
        CredentialFactory._classes = self._classes
        CredentialFactory._adapter = self._adapter

    def test_registers_custom_credential_with_matching_literal_default(
        self,
    ) -> None:
        """A correctly declared custom discriminator works end to end."""

        class CustomOpenAICredential(CredentialBase):
            """Custom OpenAI-compatible credential."""

            model_config = ConfigDict(title="Custom OpenAI API")

            type: Literal["custom_credential"] = "custom_credential"
            api_key: SecretStr = Field(description="API key.")

            @classmethod
            def get_chat_model_class(cls) -> Type[OpenAIChatModel]:
                """Return the chat model class."""
                return OpenAIChatModel

        CredentialFactory.register_credential(CustomOpenAICredential)

        credential = CredentialFactory.from_dict(
            {
                "type": "custom_credential",
                "api_key": "secret",
            },
        )

        self.assertIsInstance(credential, CustomOpenAICredential)
        self.assertIs(
            CredentialFactory.get_credential_class("custom_credential"),
            CustomOpenAICredential,
        )

    def test_rejects_literal_default_mismatch(self) -> None:
        """The #1961 class shape fails early with a clear message."""

        class BadCustomCredential(CredentialBase):
            """Credential with mismatched discriminator annotation/default."""

            type: Literal["openai_credential"] = "custom_credential"
            api_key: SecretStr = Field(description="API key.")

            @classmethod
            def get_chat_model_class(cls) -> Type[OpenAIChatModel]:
                """Return the chat model class."""
                return OpenAIChatModel

        with self.assertRaisesRegex(
            ValueError,
            (
                "BadCustomCredential.type default must match its "
                "Literal discriminator"
            ),
        ):
            CredentialFactory.register_credential(BadCustomCredential)

    def test_rejects_duplicate_discriminator(self) -> None:
        """Custom credentials cannot reuse a built-in provider type."""

        class DuplicateOpenAICredential(CredentialBase):
            """Credential that collides with the built-in OpenAI type."""

            type: Literal["openai_credential"] = "openai_credential"
            api_key: SecretStr = Field(description="API key.")

            @classmethod
            def get_chat_model_class(cls) -> Type[OpenAIChatModel]:
                """Return the chat model class."""
                return OpenAIChatModel

        with self.assertRaisesRegex(
            ValueError,
            "'openai_credential' already registered by OpenAICredential",
        ):
            CredentialFactory.register_credential(DuplicateOpenAICredential)

    def test_rejects_missing_literal_type(self) -> None:
        """Custom credentials must expose a literal ``type`` field."""

        class MissingTypeCredential(CredentialBase):
            """Credential without a discriminator type field."""

            api_key: SecretStr = Field(description="API key.")

            @classmethod
            def get_chat_model_class(cls) -> Type[OpenAIChatModel]:
                """Return the chat model class."""
                return OpenAIChatModel

        with self.assertRaisesRegex(
            ValueError,
            (
                "MissingTypeCredential must define a `type` field "
                "annotated as `Literal"
            ),
        ):
            CredentialFactory.register_credential(MissingTypeCredential)
