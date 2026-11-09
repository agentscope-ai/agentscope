# -*- coding: utf-8 -*-
"""Tests for the TypeSafe Jev classifier adapter."""
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase, skipIf
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from utils import AnyValue
from agentscope.app._router._model import model_router
from agentscope.classifier import (
    BinaryCriteria,
    BinaryQuestion,
    ChoiceQuestion,
    JevClassifierModel,
    ScoreQuestion,
)
from agentscope.credential import (
    CredentialFactory,
    OpenAICredential,
    TypeSafeCredential,
)

try:
    import typesafe_sdk
except ImportError:
    typesafe_sdk = None

A = AnyValue()


class TypeSafeCredentialTest(TestCase):
    """Test credential registration and chat model discovery without SDK."""

    def test_factory_round_trip(self) -> None:
        """The factory preserves the credential and exposes its schema."""
        credential = TypeSafeCredential(
            id="typesafe-test",
            name="TypeSafe test",
            api_key="ts-key",
            base_url="https://typesafe.example",
        )
        restored = CredentialFactory.from_dict(credential.model_dump())
        self.assertEqual(restored, credential)
        self.assertIs(
            CredentialFactory.get_credential_class("typesafe_credential"),
            TypeSafeCredential,
        )
        schemas = [
            schema
            for schema in CredentialFactory.list_schemas()
            if schema["properties"]["type"]["const"] == "typesafe_credential"
        ]
        self.assertEqual(schemas, [TypeSafeCredential.model_json_schema()])

    def test_has_no_chat_models(self) -> None:
        """Classifier credentials expose no candidate chat models."""
        self.assertEqual(TypeSafeCredential.list_models(), [])
        with self.assertRaisesRegex(
            NotImplementedError,
            "TypeSafeCredential has no chat model",
        ):
            TypeSafeCredential.get_chat_model_class()

    def test_list_models(self) -> None:
        """Return candidates, empty lists, or 404 for unknown providers."""
        app = FastAPI()
        app.include_router(model_router)
        cards = OpenAICredential.list_models()
        cases = [
            ("typesafe_credential", 200, {"models": [], "total": 0}),
            (
                "openai_credential",
                200,
                {
                    "models": [c.model_dump(mode="json") for c in cards],
                    "total": len(cards),
                },
            ),
            ("unknown", 404, {"detail": "Provider 'unknown' not found."}),
        ]
        with TestClient(app, raise_server_exceptions=False) as client:
            for provider, status, expected in cases:
                with self.subTest(provider=provider):
                    response = client.get(
                        "/model/",
                        params={"provider": provider},
                    )
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.json(), expected)


@skipIf(typesafe_sdk is None, "typesafe_sdk is not installed")
class JevClassifierModelTest(IsolatedAsyncioTestCase):
    """Test request and response translation against official SDK types."""

    @patch("typesafe_sdk.AsyncTypeSafeClient")
    async def test_call_translates_all_question_types(
        self,
        client_cls: Any,
    ) -> None:
        """All supported questions and answers should round-trip."""
        client = MagicMock()
        client.system_one = AsyncMock(
            return_value=SimpleNamespace(
                model="jev-1.13.0",
                answers={
                    "urgent": typesafe_sdk.NoulAnswer(type="noul", noul=0.8),
                    "route": typesafe_sdk.ChoiceAnswer(
                        type="choice",
                        choice="billing",
                        confidence=0.9,
                        probabilities={"billing": 0.9, "support": 0.1},
                    ),
                    "priority": typesafe_sdk.ScoreAnswer(
                        type="score",
                        score=1.7,
                        confidence=0.85,
                        legend={0: "low", 1: "medium", 2: "high"},
                        probabilities={0: 0.05, 1: 0.2, 2: 0.75},
                    ),
                },
                usage=typesafe_sdk.Usage(input_tokens=120, output_tokens=3),
            ),
        )
        client_cls.return_value = client

        model = JevClassifierModel(
            credential=TypeSafeCredential(
                api_key="secret",
                base_url="https://typesafe.example",
            ),
            max_retries=4,
            retry_delay=0.25,
        )
        response = await model(
            state="I was charged twice.",
            questions={
                "urgent": BinaryQuestion(
                    instructions="Is this urgent?",
                    criteria=BinaryCriteria(true="Urgent."),
                ),
                "route": ChoiceQuestion(
                    instructions="Select a route.",
                    criteria={"billing": None, "support": None},
                ),
                "priority": ScoreQuestion(
                    instructions="Rate priority.",
                    criteria=["low", "medium", "high"],
                ),
            },
            extra_body={"trace": True},
        )

        self.assertDictEqual(
            client_cls.call_args.kwargs,
            {
                "api_key": "secret",
                "base_url": "https://typesafe.example",
                "model": "jev-latest",
                "timeout": 30.0,
                "retry": typesafe_sdk.RetryPolicy(
                    max_retries=4,
                    backoff_initial=0.25,
                ),
            },
        )
        call_kwargs = client.system_one.await_args.kwargs
        self.assertDictEqual(
            {
                **call_kwargs,
                "questions": {
                    name: question.model_dump()
                    for name, question in call_kwargs["questions"].items()
                },
            },
            {
                "state": "I was charged twice.",
                "questions": {
                    "urgent": {
                        "type": "noul",
                        "instructions": "Is this urgent?",
                        "criteria": {"true": "Urgent."},
                    },
                    "route": {
                        "type": "choice",
                        "instructions": "Select a route.",
                        "criteria": {"billing": None, "support": None},
                    },
                    "priority": {
                        "type": "score",
                        "instructions": "Rate priority.",
                        "criteria": ["low", "medium", "high"],
                    },
                },
                "model": "jev-latest",
                "extra_body": {"trace": True},
            },
        )
        self.assertDictEqual(
            asdict(response),
            {
                "model": "jev-1.13.0",
                "content": {
                    "urgent": {
                        "probability": 0.8,
                        "type": "binary_answer",
                    },
                    "route": {
                        "choice": "billing",
                        "confidence": 0.9,
                        "probabilities": {
                            "billing": 0.9,
                            "support": 0.1,
                        },
                        "type": "choice_answer",
                    },
                    "priority": {
                        "score": 1.7,
                        "confidence": 0.85,
                        "legend": {0: "low", 1: "medium", 2: "high"},
                        "probabilities": {0: 0.05, 1: 0.2, 2: 0.75},
                        "type": "score_answer",
                    },
                },
                "usage": {
                    "time": A,
                    "input_tokens": 120,
                    "output_tokens": 3,
                    "type": "classifier",
                },
                "id": A,
                "created_at": A,
                "type": "classifier_response",
                "metadata": {},
            },
        )

    @patch("typesafe_sdk.AsyncTypeSafeClient")
    async def test_provider_error_is_raised(self, client_cls: Any) -> None:
        """Retries belong to the SDK, so its errors are raised as they are."""
        client = MagicMock()
        client.system_one = AsyncMock(side_effect=RuntimeError("failed"))
        client_cls.return_value = client
        model = JevClassifierModel(TypeSafeCredential(api_key="secret"))

        with self.assertRaisesRegex(RuntimeError, "failed"):
            await model(
                state="hello",
                questions={"route": ChoiceQuestion(criteria={"a": None})},
            )

        self.assertDictEqual(
            client_cls.call_args.kwargs,
            {
                "api_key": "secret",
                "base_url": None,
                "model": "jev-latest",
                "timeout": 30.0,
                "retry": typesafe_sdk.RetryPolicy(),
            },
        )

    async def test_incompatible_sdk_has_clear_error(self) -> None:
        """Missing SDK exports should produce an actionable error."""
        incompatible_sdk = SimpleNamespace(AsyncTypeSafeClient=MagicMock())

        with patch.dict("sys.modules", {"typesafe_sdk": incompatible_sdk}):
            with self.assertRaisesRegex(
                ImportError,
                "requires a compatible optional",
            ):
                JevClassifierModel(TypeSafeCredential(api_key="secret"))

    @staticmethod
    def _response(model: str) -> SimpleNamespace:
        """Build a System One response carrying one noul answer."""
        return SimpleNamespace(
            model=model,
            answers={
                "urgent": typesafe_sdk.NoulAnswer(type="noul", noul=0.8),
            },
            usage=typesafe_sdk.Usage(input_tokens=7, output_tokens=1),
        )

    @patch("typesafe_sdk.AsyncTypeSafeClient")
    async def test_per_call_model_replaces_the_default(
        self,
        client_cls: Any,
    ) -> None:
        """``model=`` is forwarded once, with the caller's value."""
        client = MagicMock()
        client.system_one = AsyncMock(return_value=self._response("jev-1.5"))
        client_cls.return_value = client
        model = JevClassifierModel(
            TypeSafeCredential(api_key="secret"),
            model="jev-latest",
        )

        await model(
            state="I was charged twice.",
            questions={"urgent": BinaryQuestion(instructions="Urgent?")},
            model="jev-1.5",
            extra_body={"trace": True},
        )

        call_kwargs = client.system_one.await_args.kwargs
        self.assertDictEqual(
            {
                **call_kwargs,
                "questions": {
                    name: question.model_dump()
                    for name, question in call_kwargs["questions"].items()
                },
            },
            {
                "state": "I was charged twice.",
                "questions": {
                    "urgent": {
                        "type": "noul",
                        "instructions": "Urgent?",
                    },
                },
                "model": "jev-1.5",
                "extra_body": {"trace": True},
            },
        )

    @patch("typesafe_sdk.AsyncTypeSafeClient")
    async def test_per_call_model_is_not_remembered(
        self,
        client_cls: Any,
    ) -> None:
        """The instance default applies again on the next call."""
        client = MagicMock()
        client.system_one = AsyncMock(return_value=self._response("jev-1.5"))
        client_cls.return_value = client
        model = JevClassifierModel(
            TypeSafeCredential(api_key="secret"),
            model="jev-latest",
        )
        questions = {"urgent": BinaryQuestion(instructions="Urgent?")}

        await model(state="one", questions=questions, model="jev-1.5")
        await model(state="two", questions=questions)

        self.assertListEqual(
            [
                call.kwargs["model"]
                for call in client.system_one.await_args_list
            ],
            ["jev-1.5", "jev-latest"],
        )
