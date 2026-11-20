# -*- coding: utf-8 -*-
"""Unit tests for :func:`get_model` — the chat model factory."""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError

from agentscope.app._service._model import get_model
from agentscope.app.storage import ChatModelConfig
from agentscope.credential import CredentialFactory, OpenAICredential
from agentscope.model import (
    ChatModelBase,
    ChatResponse,
    DeepSeekChatModel,
    ModelCard,
    OpenAIChatModel,
    OpenAIResponseModel,
)

_CREDENTIAL = {"type": "deepseek_credential", "api_key": "test"}


class _CustomOpenAIChatModel(OpenAIChatModel):
    """A custom adapter selected through the legacy credential getter."""


class _CustomOpenAICredential(OpenAICredential):
    """An existing credential extension using the single-class getter."""

    @classmethod
    def get_chat_model_class(cls) -> type[OpenAIChatModel]:
        return _CustomOpenAIChatModel


class OpenAICredentialModelTest(IsolatedAsyncioTestCase):
    """Check model resolution for built-in and extended credentials."""

    def test_default_models(self) -> None:
        """Standard credentials continue to expose both API surfaces."""
        self.assertListEqual(
            OpenAICredential.get_chat_model_classes(),
            [OpenAIChatModel, OpenAIResponseModel],
        )

    def test_legacy_model_override(self) -> None:
        """A legacy override keeps its model without adding other APIs."""
        for model_cls in (_CustomOpenAIChatModel, OpenAIResponseModel):
            with self.subTest(model=model_cls.__name__):
                with patch.object(
                    _CustomOpenAICredential,
                    "get_chat_model_class",
                    return_value=model_cls,
                ):
                    self.assertListEqual(
                        _CustomOpenAICredential.get_chat_model_classes(),
                        [model_cls],
                    )

    async def test_factory_uses_legacy_model_override(self) -> None:
        """Saved configs without model_class still use the custom adapter."""
        access = MagicMock(
            resolve_credential=AsyncMock(
                return_value=SimpleNamespace(data={}),
            ),
        )
        credential = _CustomOpenAICredential(api_key="test")
        with patch.object(
            CredentialFactory,
            "from_dict",
            return_value=credential,
        ), patch.object(
            _CustomOpenAICredential,
            "get_chat_model_classes",
            return_value=[OpenAIResponseModel, _CustomOpenAIChatModel],
        ) as get_classes:
            model = await get_model(
                "user-1",
                ChatModelConfig(
                    type="openai_credential",
                    credential_id="cred-1",
                    model="custom-model",
                    parameters={"max_tokens": 123},
                ),
                access,
            )
        try:
            get_classes.assert_not_called()
            self.assertEqual(
                (type(model), model.model, model.parameters.max_tokens),
                (_CustomOpenAIChatModel, "custom-model", 123),
            )
        finally:
            await model.client.close()

    async def test_explicit_model_class(self) -> None:
        """Explicit classes select the API for built-in and custom names."""
        access = MagicMock(
            resolve_credential=AsyncMock(
                return_value=SimpleNamespace(
                    data={"type": "openai_credential", "api_key": "test"},
                ),
            ),
        )
        for model_cls in (OpenAIChatModel, OpenAIResponseModel):
            for name in ("gpt-4o", "custom-deployment"):
                with self.subTest(model_class=model_cls.type, model=name):
                    model = await get_model(
                        "user-1",
                        ChatModelConfig(
                            type="openai_credential",
                            credential_id="cred-1",
                            model=name,
                            model_class=model_cls.type,
                            parameters={"max_tokens": 123},
                        ),
                        access,
                    )
                    try:
                        self.assertEqual(
                            (
                                type(model),
                                model.model,
                                model.parameters.max_tokens,
                            ),
                            (model_cls, name, 123),
                        )
                    finally:
                        await model.client.close()

    async def test_unsupported_model_class_raises(self) -> None:
        """Invalid or foreign adapter types cannot silently fall back."""
        access = MagicMock(
            resolve_credential=AsyncMock(
                return_value=SimpleNamespace(
                    data={"type": "openai_credential", "api_key": "test"},
                ),
            ),
        )
        for model_class in ("unknown", "deepseek_chat"):
            with self.subTest(model_class=model_class):
                with self.assertRaises(ValueError) as raised:
                    model = await get_model(
                        "user-1",
                        ChatModelConfig(
                            type="openai_credential",
                            credential_id="cred-1",
                            model="gpt-4o",
                            model_class=model_class,
                            parameters={},
                        ),
                        access,
                    )
                    await model.client.close()
                self.assertEqual(
                    str(raised.exception),
                    f"Chat model class {model_class!r} is not available "
                    "for credential 'cred-1'.",
                )


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
        model_class: str = "",
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
                model_class=model_class,
                parameters=parameters,
            ),
            access,
        )
        self.addAsyncCleanup(model.client.close)
        return model

    async def test_explicit_model_class_uses_its_card_defaults(self) -> None:
        """Card defaults belong to the selected adapter, not the legacy one."""
        card = ModelCard(
            name="shared-model",
            label="Shared model",
            status="active",
            context_size=64000,
            output_size=4096,
            parameter_schema={},
            parameters_overrides={
                "max_tokens": {"default": 1024},
                "temperature": {"default": 0.5},
            },
        )
        with patch.object(
            OpenAIResponseModel,
            "list_models",
            return_value=[card],
        ):
            model = await self._build_model(
                {"temperature": 0},
                model_name=card.name,
                credential_type="openai_credential",
                model_class=OpenAIResponseModel.type,
            )

        self.assertIsInstance(model, OpenAIResponseModel)
        self.assertEqual(1024, model.parameters.max_tokens)
        self.assertEqual(0, model.parameters.temperature)

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
