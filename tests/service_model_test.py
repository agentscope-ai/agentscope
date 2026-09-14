# -*- coding: utf-8 -*-
"""Tests for chat-model construction in the app service."""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pydantic import BaseModel

from agentscope.app._service._model import get_model
from agentscope.app.storage import ChatModelConfig, CredentialRecord


class _FakeChatModel:
    """Minimal model double that records the service constructor inputs."""

    class Parameters(BaseModel):
        """No provider-specific parameters are needed for this test."""

    @classmethod
    def list_models(cls) -> list[SimpleNamespace]:
        """Return one model card whose window differs from the default."""
        return [
            SimpleNamespace(
                name="card-model",
                context_size=1_000_000,
                input_types=["text/plain", "image/jpeg"],
            ),
        ]

    def __init__(
        self,
        credential: object,
        model: str,
        parameters: BaseModel | None,
        context_size: int = 32_768,
    ) -> None:
        """Record construction without making a provider request."""
        self.credential = credential
        self.model = model
        self.parameters = parameters
        self.context_size = context_size
        self.formatter = SimpleNamespace(input_types=["text/plain"])


class _FakeCredential:
    """Credential double that resolves to :class:`_FakeChatModel`."""

    @classmethod
    def get_chat_model_class(cls) -> type[_FakeChatModel]:
        """Return the model class used by the service."""
        return _FakeChatModel


class _FakeAccess:
    """Access-service double returning one raw credential record."""

    async def resolve_credential(
        self,
        _user_id: str,
        _credential_id: str,
    ) -> CredentialRecord:
        """Return the credential record consumed by the factory."""
        return CredentialRecord(
            id="credential", user_id="user", data={"type": "fake"}
        )


class GetModelTest(IsolatedAsyncioTestCase):
    """The app-service model factory honors built-in model cards."""

    async def test_uses_matching_model_card_context_size(self) -> None:
        """A card's context window reaches the constructed chat model."""
        config = ChatModelConfig(
            type="fake",
            credential_id="credential",
            model="card-model",
            parameters={},
        )
        with patch(
            "agentscope.app._service._model.CredentialFactory.from_dict",
            return_value=_FakeCredential(),
        ):
            model = await get_model("user", config, _FakeAccess())

        self.assertEqual(model.context_size, 1_000_000)
        self.assertEqual(
            model.formatter.input_types, ["text/plain", "image/jpeg"]
        )
