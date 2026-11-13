# -*- coding: utf-8 -*-
# pylint: disable=missing-class-docstring,missing-function-docstring
"""Unit tests for embedding model construction from stored credentials."""
from unittest import TestCase

from fastapi import HTTPException

from agentscope.app._service._embedding import build_embedding_model
from agentscope.app.storage import CredentialRecord, EmbeddingModelConfig


class EmbeddingCredentialTypeTest(TestCase):
    """The configured provider must use the stored credential's provider."""

    def test_rejects_mismatched_credential_provider(self) -> None:
        """Do not hand one provider's key to another provider's model."""
        record = CredentialRecord(
            id="credential-1",
            user_id="user-1",
            data={
                "type": "openai_credential",
                "api_key": "sk-test",
            },
        )
        config = EmbeddingModelConfig(
            type="dashscope_credential",
            credential_id=record.id,
            model="text-embedding-v4",
            dimensions=1024,
        )

        with self.assertRaises(HTTPException) as context:
            build_embedding_model(record, config)

        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(
            context.exception.detail,
            "Embedding model provider does not match the credential type.",
        )
