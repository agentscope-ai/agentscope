# -*- coding: utf-8 -*-
"""Tests for constructing embedding models from stored credentials."""
from unittest import TestCase

from fastapi import HTTPException

from agentscope.app._service._embedding import build_embedding_model
from agentscope.app.storage import CredentialRecord, EmbeddingModelConfig
from agentscope.embedding import DashScopeEmbeddingModel


class EmbeddingCredentialTypeTest(TestCase):
    """A saved model provider must match its selected credential."""

    def test_rejects_mismatched_credential_provider(self) -> None:
        """Do not pass one provider's API key to another provider client."""
        record = CredentialRecord(
            id="credential-1",
            user_id="user-1",
            data={"type": "openai_credential", "api_key": "test-key"},
        )
        config = EmbeddingModelConfig(
            type="dashscope_credential",
            credential_id=record.id,
            model="text-embedding-v4",
            dimensions=1024,
        )

        with self.assertRaises(HTTPException) as raised:
            build_embedding_model(record, config)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("does not match", raised.exception.detail)

    def test_matching_provider_still_builds_embedding_model(self) -> None:
        """A matching credential keeps the normal construction path."""
        record = CredentialRecord(
            id="credential-1",
            user_id="user-1",
            data={"type": "dashscope_credential", "api_key": "test-key"},
        )
        config = EmbeddingModelConfig(
            type="dashscope_credential",
            credential_id=record.id,
            model="text-embedding-v4",
            dimensions=1024,
        )

        model = build_embedding_model(record, config)

        self.assertIsInstance(model, DashScopeEmbeddingModel)
        self.assertEqual(model.credential.type, config.type)
