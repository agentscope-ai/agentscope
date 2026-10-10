# -*- coding: utf-8 -*-
"""Tests for the TypeSafe Jev classifier adapter."""
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils import AnyValue
from agentscope.classifier import (
    BinaryCriteria,
    BinaryQuestion,
    ChoiceQuestion,
    JevClassifierModel,
    ScoreQuestion,
)
from agentscope.credential import TypeSafeCredential

typesafe_sdk = pytest.importorskip("typesafe_sdk")

A = AnyValue()


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
