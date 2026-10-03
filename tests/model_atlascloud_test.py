# -*- coding: utf-8 -*-
"""Unit tests for AtlasCloudChatModel."""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.credential import AtlasCloudCredential, CredentialFactory
from agentscope.model import AtlasCloudChatModel


class TestAtlasCloudModel(IsolatedAsyncioTestCase):
    """Tests for Atlas Cloud credential and chat model wiring."""

    def test_credential_factory_and_model_cards(self) -> None:
        """Atlas Cloud is available through credentials and model cards."""
        credential = CredentialFactory.from_dict(
            {
                "type": "atlascloud_credential",
                "api_key": "test",
            },
        )

        self.assertIsInstance(credential, AtlasCloudCredential)
        self.assertEqual(
            credential.base_url,
            "https://api.atlascloud.ai/v1",
        )
        self.assertIs(
            AtlasCloudCredential.get_chat_model_class(),
            AtlasCloudChatModel,
        )
        self.assertIn(
            "qwen/qwen3.5-flash",
            [card.name for card in AtlasCloudChatModel.list_models()],
        )

    @patch("openai.AsyncClient")
    async def test_uses_atlascloud_openai_compatible_client(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Atlas Cloud forwards requests through the OpenAI-compatible path."""
        message = MagicMock()
        message.content = "ok"
        message.reasoning_content = None
        message.reasoning = None
        message.audio = None
        message.tool_calls = None

        choice = MagicMock()
        choice.message = message

        response = MagicMock()
        response.id = "atlascloud-1"
        response.choices = [choice]
        response.usage.prompt_tokens = 1
        response.usage.completion_tokens = 1
        response.usage.prompt_tokens_details = None

        mock_create = AsyncMock(return_value=response)
        mock_client_cls.return_value.chat.completions.create = mock_create

        model = AtlasCloudChatModel(
            credential=AtlasCloudCredential(api_key="test"),
            stream=False,
        )

        result = await model([])

        self.assertEqual(result.id, "atlascloud-1")
        mock_client_cls.assert_called_once()
        self.assertEqual(
            mock_client_cls.call_args.kwargs["base_url"],
            "https://api.atlascloud.ai/v1",
        )
        self.assertEqual(
            mock_create.call_args.kwargs["model"],
            "qwen/qwen3.5-flash",
        )
