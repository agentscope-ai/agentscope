# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Service-level regression test for `build_embedding_model`.

Locks down the `pass_dimensions` forwarding behaviour introduced to gate
the OpenAI `dimensions` API parameter on matryoshka capability. The other
three providers (DashScope / Gemini / Ollama) do not accept this kwarg,
so the service must not forward it to them.

Background:

- Only ``OpenAIEmbeddingModel.__init__`` accepts ``pass_dimensions``.
- The service uses :func:`inspect.signature` to detect that and only
  attaches the kwarg when the embedding class declares it.
- For an OpenAI matryoshka-capable card (``supported_dimensions`` set),
  ``pass_dimensions`` is set to ``True``; otherwise ``False``.
- For the three non-OpenAI providers the kwarg must not appear at all,
  even when ``supported_dimensions`` is set on the underlying card.
"""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from agentscope.app._service._embedding import build_embedding_model
from agentscope.app.storage import CredentialRecord, EmbeddingModelConfig


def _credential() -> CredentialRecord:
    """Build a minimal ``CredentialRecord``."""
    return CredentialRecord(id="cred-1", user_id="u", data={"api_key": "sk"})


def _config(
    model: str,
    dimensions: int = 1024,
    type_: str = "openai",
) -> EmbeddingModelConfig:
    """Build a minimal ``EmbeddingModelConfig``."""
    return EmbeddingModelConfig(
        type=type_,
        model=model,
        dimensions=dimensions,
        parameters={},
        credential_id="cred-1",
    )


def _card(
    name: str,
    *,
    supported_dimensions: list[int] | None,
    context_size: int | None = 8191,
) -> MagicMock:
    """Build a fake model card with the given matryoshka support."""
    card = MagicMock()
    card.name = name
    card.supported_dimensions = supported_dimensions
    card.context_size = context_size
    return card


class _StubOpenAIEmbeddingModel:
    """Stub of ``OpenAIEmbeddingModel`` with a real ``__init__`` signature
    so the service's :func:`inspect.signature` check correctly identifies
    ``pass_dimensions`` as a supported kwarg.

    A ``MagicMock`` cannot stand in here because its ``__init__`` is the
    mock's own ``(*args, **kwargs)`` signature, which never contains
    ``pass_dimensions`` — the service would then skip the kwarg for the
    stub and the OpenAI branch would never be exercised.
    """

    instances: list = []

    def __init__(  # type: ignore[no-untyped-def]
        self,
        credential,
        model,
        dimensions,
        parameters=None,
        pass_dimensions=True,
        embedding_cache=None,
        context_size=8191,
        max_retries=3,
        retry_delay=1.0,
    ):
        self.kwargs = {
            "credential": credential,
            "model": model,
            "dimensions": dimensions,
            "parameters": parameters,
            "pass_dimensions": pass_dimensions,
            "embedding_cache": embedding_cache,
            "context_size": context_size,
            "max_retries": max_retries,
            "retry_delay": retry_delay,
        }
        type(self).instances.append(self)

    @classmethod
    def list_models(cls) -> list:
        """Return the controlled model-card list for the test stub."""
        return cls._card_list

    @classmethod
    def reset(cls) -> None:
        """Reset the stub's instance / card-list state between tests."""
        cls.instances = []
        cls._card_list = []


class OpenAIPassDimensionsTest(TestCase):
    """The OpenAI-specific ``pass_dimensions`` flag must be set according
    to whether the model card declares ``supported_dimensions``."""

    def _run_with_card(
        self,
        card: MagicMock,
        model: str = "text-embedding-3-small",
        type_: str = "openai",
    ) -> dict:
        """Drive ``build_embedding_model`` with a fake OpenAI card and
        return the kwargs the embedding class was instantiated with."""
        _StubOpenAIEmbeddingModel.reset()
        _StubOpenAIEmbeddingModel._card_list = [card]
        with patch(
            "agentscope.credential.CredentialFactory.from_dict",
            return_value=MagicMock(),
        ), patch(
            "agentscope.credential.CredentialFactory.get_credential_class",
        ) as MockCredClass, patch(
            "agentscope.embedding._openai._model.OpenAIEmbeddingModel",
            new=_StubOpenAIEmbeddingModel,
        ):
            cred_cls = MockCredClass.return_value
            cred_cls.get_embedding_model_class.return_value = (
                _StubOpenAIEmbeddingModel
            )
            build_embedding_model(
                _credential(),
                _config(model, type_=type_),
            )
        return _StubOpenAIEmbeddingModel.instances[-1].kwargs

    def test_matryoshka_card_sets_pass_dimensions_true(self) -> None:
        """A model card with ``supported_dimensions`` set must produce
        ``pass_dimensions=True`` so the OpenAI client forwards the user
        dimension to the API."""
        kwargs = self._run_with_card(
            _card(
                "text-embedding-3-small",
                supported_dimensions=[1536, 1024, 768, 512, 256],
            ),
        )
        self.assertTrue(
            kwargs.get("pass_dimensions"),
            "matryoshka-capable card should set pass_dimensions=True",
        )

    def test_non_matryoshka_card_sets_pass_dimensions_false(self) -> None:
        """A model card with ``supported_dimensions=None`` must produce
        ``pass_dimensions=False`` so the OpenAI client falls back to the
        model's default dimension."""
        kwargs = self._run_with_card(
            _card("text-embedding-ada-002", supported_dimensions=None),
            model="text-embedding-ada-002",
        )
        self.assertFalse(
            kwargs.get("pass_dimensions"),
            "non-matryoshka card should set pass_dimensions=False",
        )

    def test_unlisted_openai_model_sets_pass_dimensions_false(self) -> None:
        """An OpenAI model not present in ``list_models()`` (for example
        a custom proxy entry) must not crash and must default to
        ``pass_dimensions=False`` so the client uses its default size."""
        # Card only contains text-embedding-3-small, but the config
        # requests a different, unlisted model.
        kwargs = self._run_with_card(
            _card(
                "text-embedding-3-small",
                supported_dimensions=[1536, 1024, 768, 512, 256],
            ),
            model="text-embedding-custom-proxy-7",
        )
        self.assertFalse(
            kwargs.get("pass_dimensions"),
            "unlisted model should default to pass_dimensions=False",
        )

    def test_context_size_forwarded_from_card(self) -> None:
        """Independent of ``pass_dimensions``, ``context_size`` from the
        model card must still be forwarded when present."""
        kwargs = self._run_with_card(
            _card(
                "text-embedding-3-small",
                supported_dimensions=[1536, 1024, 768, 512, 256],
                context_size=8191,
            ),
        )
        self.assertEqual(kwargs.get("context_size"), 8191)


class NonOpenAIPassDimensionsOmittedTest(TestCase):
    """The ``pass_dimensions`` kwarg must not be forwarded to providers
    whose embedding class does not declare it. Sending it would raise
    ``TypeError: __init__() got an unexpected keyword argument`` at
    instantiation time."""

    def _assert_kwarg_absent(
        self,
        *,
        provider_module: str,
        embedding_class_name: str,
        type_: str,
        supported_dimensions: list[int] | None,
    ) -> None:
        """Run ``build_embedding_model`` for the given provider and
        assert the call to the embedding class does **not** include
        ``pass_dimensions`` in its kwargs.

        We use a ``MagicMock`` here because the production code's
        :func:`inspect.signature` check should detect the absence of
        ``pass_dimensions`` on the mock's own ``__init__`` and skip the
        kwarg entirely.
        """
        with patch(
            "agentscope.credential.CredentialFactory.from_dict",
            return_value=MagicMock(),
        ), patch(
            "agentscope.credential.CredentialFactory.get_credential_class",
        ) as MockCredClass, patch(
            f"{provider_module}.{embedding_class_name}",
        ) as MockModel:
            MockModel.return_value = MagicMock()
            MockModel.list_models.return_value = [
                _card("any-model", supported_dimensions=supported_dimensions),
            ]
            MockModel.Parameters = MagicMock()
            cred_cls = MockCredClass.return_value
            cred_cls.get_embedding_model_class.return_value = MockModel
            build_embedding_model(
                _credential(),
                _config("any-model", type_=type_),
            )
            self.assertNotEqual(
                MockModel.call_args,
                None,
                f"{embedding_class_name} should have been called",
            )
            _, kwargs = MockModel.call_args
            self.assertNotIn(
                "pass_dimensions",
                kwargs,
                f"{embedding_class_name} must not receive pass_dimensions",
            )

    def test_dashscope_does_not_receive_pass_dimensions(self) -> None:
        """DashScope has no ``pass_dimensions`` kwarg — the kwarg must
        be omitted even when the underlying card advertises matryoshka."""
        self._assert_kwarg_absent(
            provider_module="agentscope.embedding._dashscope._model",
            embedding_class_name="DashScopeEmbeddingModel",
            type_="dashscope",
            supported_dimensions=[1024, 768, 512, 256, 128, 64],
        )

    def test_gemini_does_not_receive_pass_dimensions(self) -> None:
        """Gemini has no ``pass_dimensions`` kwarg — the kwarg must be
        omitted even when the underlying card advertises matryoshka."""
        self._assert_kwarg_absent(
            provider_module="agentscope.embedding._gemini._model",
            embedding_class_name="GeminiEmbeddingModel",
            type_="gemini",
            supported_dimensions=[3072, 1536, 768, 512, 256, 128],
        )

    def test_ollama_does_not_receive_pass_dimensions(self) -> None:
        """Ollama has no ``pass_dimensions`` kwarg — the kwarg must be
        omitted even when the underlying card advertises matryoshka."""
        self._assert_kwarg_absent(
            provider_module="agentscope.embedding._ollama._model",
            embedding_class_name="OllamaEmbeddingModel",
            type_="ollama",
            supported_dimensions=[768, 512, 256, 128],
        )
