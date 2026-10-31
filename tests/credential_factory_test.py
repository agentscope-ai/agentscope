# -*- coding: utf-8 -*-
"""Test cases for CredentialFactory."""
import json
from unittest import TestCase

from agentscope.credential import CredentialFactory, TypeSafeCredential


class CredentialFactoryTest(TestCase):
    """Test the built-in registration coverage of CredentialFactory."""

    def test_builtin_typesafe_credential_round_trip(self) -> None:
        """Every exported built-in credential round-trips the factory.

        ``TypeSafeCredential`` is exported from ``agentscope.credential``
        but was never registered in the factory, so a stored record of it
        failed to deserialize, ``get_credential_class`` returned ``None``
        and it was missing from ``list_schemas``.
        """
        credential = TypeSafeCredential(api_key="ts-key")

        restored = CredentialFactory.from_dict(credential.model_dump())

        self.assertEqual(restored.api_key.get_secret_value(), "ts-key")
        self.assertIs(
            CredentialFactory.get_credential_class("typesafe_credential"),
            TypeSafeCredential,
        )
        schemas = CredentialFactory.list_schemas()
        self.assertTrue(
            any(
                "typesafe_credential" in json.dumps(schema)
                for schema in schemas
            ),
        )
