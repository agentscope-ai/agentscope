# -*- coding: utf-8 -*-
"""Unit tests for :func:`get_model` — the chat model factory."""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError

from agentscope.app._service._model import get_model
from agentscope.app.storage import ChatModelConfig
from agentscope.credential import CredentialFactory
from agentscope.model import (
    ChatModelBase,
    ChatResponse,
    DeepSeekChatModel,
    ModelCard,
)

_CREDENTIAL = {"type": "deepseek_credential", "api_key": "test"}


class GetModelContextSizeTest(IsolatedAsyncioTestCase):
    """The factory must forward the model card's ``context_size``."""

    async def test_uses_card_context_size(self) -> None:
        """A matching card's value wins over the class default."""
        access = MagicMock(
            resolve_credential=AsyncMock(
                return_value=SimpleNamespace(data=_CREDENTIAL),
            ),
        )

        model = await get_model(
            "user-1",
            ChatModelConfig(
                type="deepseek",
                credential_id="cred-1",
                model="deepseek-v4-flash",
                parameters={},
            ),
            access,
        )

        self.assertEqual(1000000, model.context_size)

    async def test_keeps_default_for_unknown_model(self) -> None:
        """A model without a card keeps the constructor default."""
        access = MagicMock(
            resolve_credential=AsyncMock(
                return_value=SimpleNamespace(data=_CREDENTIAL),
            ),
        )

        model = await get_model(
            "user-1",
            ChatModelConfig(
                type="deepseek",
                credential_id="cred-1",
                model="my-custom-model",
                parameters={},
            ),
            access,
        )

        self.assertEqual(65536, model.context_size)


class GetModelCardDefaultsTest(IsolatedAsyncioTestCase):
    """Card defaults apply only when the configuration omits a parameter."""

    async def _build_model(
        self,
        parameters: dict,
        model_name: str = "claude-opus-4-8",
        credential_type: str = "anthropic_credential",
    ) -> ChatModelBase:
        access = SimpleNamespace(
            resolve_credential=AsyncMock(
                return_value=SimpleNamespace(
                    data={"type": credential_type, "api_key": "test"},
                ),
            ),
        )
        model = await get_model(
            "user-1",
            ChatModelConfig(
                type=credential_type.removesuffix("_credential"),
                credential_id="cred-1",
                model=model_name,
                parameters=parameters,
            ),
            access,
        )
        self.addAsyncCleanup(model.client.close)
        return model

    async def test_applies_shipped_card_defaults_to_empty_parameters(
        self,
    ) -> None:
        """Selecting Opus without editing parameters enables its defaults."""
        model = await self._build_model({})

        self.assertEqual("adaptive", model.parameters.thinking_mode)
        self.assertEqual("summarized", model.parameters.thinking_display)
        # A maximum in the card is a constraint, not a default.
        self.assertIsNone(model.parameters.max_tokens)
        self.assertEqual(1000000, model.context_size)

    async def test_partial_configuration_keeps_missing_card_defaults(
        self,
    ) -> None:
        """Setting one parameter does not discard defaults for the others."""
        model = await self._build_model({"max_tokens": 1024})

        self.assertEqual(1024, model.parameters.max_tokens)
        self.assertEqual("adaptive", model.parameters.thinking_mode)
        self.assertEqual("summarized", model.parameters.thinking_display)

    async def test_explicit_values_override_card_defaults(self) -> None:
        """Users can disable thinking and omit summaries explicitly."""
        model = await self._build_model(
            {"thinking_mode": "disabled", "thinking_display": "omitted"},
        )

        self.assertEqual("disabled", model.parameters.thinking_mode)
        self.assertEqual("omitted", model.parameters.thinking_display)

    async def test_explicit_null_overrides_card_defaults(self) -> None:
        """A configured null is not treated as an omitted parameter."""
        model = await self._build_model(
            {"thinking_mode": None, "thinking_display": None},
        )

        self.assertIsNone(model.parameters.thinking_mode)
        self.assertIsNone(model.parameters.thinking_display)

    async def test_falsy_defaults_are_applied_without_mutating_the_card(
        self,
    ) -> None:
        """Zero and false defaults must survive default extraction."""
        overrides = {
            "temperature": {"default": 0},
            "thinking_enable": {"default": False},
            "max_tokens": {"maximum": 4096},
        }
        card = ModelCard(
            name="card-test",
            label="Card test",
            status="active",
            context_size=64000,
            output_size=4096,
            parameter_schema={},
            parameters_overrides=overrides,
        )
        with patch.object(
            DeepSeekChatModel,
            "list_models",
            return_value=[card],
        ):
            model = await self._build_model(
                {},
                model_name="card-test",
                credential_type="deepseek_credential",
            )

        self.assertEqual(0, model.parameters.temperature)
        self.assertFalse(model.parameters.thinking_enable)
        self.assertIsNone(model.parameters.max_tokens)
        self.assertEqual(overrides, card.parameters_overrides)

    async def test_unknown_model_preserves_explicit_parameters(self) -> None:
        """Custom model names still use the provider Parameters defaults."""
        model = await self._build_model(
            {"max_tokens": 1024},
            model_name="custom-model",
        )

        self.assertEqual(1024, model.parameters.max_tokens)
        self.assertIsNone(model.parameters.thinking_mode)
        self.assertEqual(200000, model.context_size)

    async def test_card_lookup_failure_keeps_provider_defaults(self) -> None:
        """Optional card discovery does not prevent model construction."""
        with patch.object(
            DeepSeekChatModel,
            "list_models",
            side_effect=RuntimeError("card discovery failed"),
        ):
            model = await self._build_model(
                {"temperature": 0},
                model_name="custom-model",
                credential_type="deepseek_credential",
            )

        self.assertEqual(0, model.parameters.temperature)
        self.assertEqual(65536, model.context_size)

    async def test_card_defaults_are_validated_as_provider_parameters(
        self,
    ) -> None:
        """Invalid defaults cannot bypass provider parameter validation."""
        card = ModelCard(
            name="card-test",
            label="Card test",
            status="active",
            context_size=64000,
            output_size=4096,
            parameter_schema={},
            parameters_overrides={"temperature": {"default": -1}},
        )
        with patch.object(
            DeepSeekChatModel,
            "list_models",
            return_value=[card],
        ):
            with self.assertRaises(ValidationError):
                await self._build_model(
                    {},
                    model_name="card-test",
                    credential_type="deepseek_credential",
                )

    async def test_custom_model_without_formatter_still_builds(self) -> None:
        """A formatter is not part of the ChatModelBase contract."""

        class CustomChatModel(ChatModelBase):
            """A custom model that does not expose a formatter."""

            async def _call_api(
                self,
                *_args: object,
                **_kwargs: object,
            ) -> ChatResponse:
                """The construction-only test never invokes the model."""
                return ChatResponse(content=[], is_last=True)

        card = ModelCard(
            name="custom-card",
            label="Custom card",
            status="active",
            context_size=64000,
            output_size=4096,
            parameter_schema={},
            parameters_overrides={},
        )
        credential = SimpleNamespace(
            get_chat_model_class=lambda: CustomChatModel,
        )
        access = SimpleNamespace(
            resolve_credential=AsyncMock(
                return_value=SimpleNamespace(data={}),
            ),
        )
        with (
            patch.object(
                CredentialFactory,
                "from_dict",
                return_value=credential,
            ),
            patch.object(CustomChatModel, "list_models", return_value=[card]),
        ):
            model = await get_model(
                "user-1",
                ChatModelConfig(
                    type="custom",
                    credential_id="cred-1",
                    model=card.name,
                    parameters={},
                ),
                access,
            )

        self.assertIsInstance(model, CustomChatModel)
        self.assertEqual(64000, model.context_size)
