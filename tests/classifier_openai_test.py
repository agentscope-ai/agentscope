# -*- coding: utf-8 -*-
"""Tests for the OpenAI Decisions classifier adapter."""
from dataclasses import asdict
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyValue
from agentscope import classifier
from agentscope.classifier import (
    BinaryCriteria,
    BinaryQuestion,
    ChoiceQuestion,
    ScoreQuestion,
)
from agentscope.credential import OpenAICredential
from agentscope.message import Base64Source, DataBlock, TextBlock, URLSource


class OpenAIClassifierModelTest(IsolatedAsyncioTestCase):
    """Exercise request and response translation without network calls."""

    def setUp(self) -> None:
        """Replace the OpenAI client with an asynchronous stub."""
        client_patch = patch("openai.AsyncOpenAI")
        self.client_cls = client_patch.start()
        self.addCleanup(client_patch.stop)
        self.client = self.client_cls.return_value
        self.client.decisions.create = AsyncMock(
            return_value=SimpleNamespace(
                model="gpt-6-luna",
                answers=[],
                usage=SimpleNamespace(input_tokens=12, output_tokens=0),
            ),
        )
        self.credential = OpenAICredential(api_key="secret")

    async def test_call_translates_all_answers(self) -> None:
        """Keep all answers, including a refusal, and provider usage."""
        self.client.decisions.create.return_value.answers = [
            SimpleNamespace(type="predicate", name="urgent", probability=0.8),
            SimpleNamespace(
                type="choice",
                name="route",
                choice="billing",
                confidence=0.9,
                probabilities=[
                    SimpleNamespace(value="billing", probability=0.9),
                    SimpleNamespace(value="support", probability=0.1),
                ],
            ),
            SimpleNamespace(
                type="score",
                name="priority",
                score=1.7,
                confidence=0.85,
                probabilities=[
                    SimpleNamespace(value=0, label="low", probability=0.05),
                    SimpleNamespace(value=1, label="medium", probability=0.2),
                    SimpleNamespace(value=2, label="high", probability=0.75),
                ],
            ),
            SimpleNamespace(type="refusal", name="restricted"),
        ]
        model = classifier.OpenAIClassifierModel(
            OpenAICredential(
                api_key="secret",
                organization="org-test",
                base_url="https://openai.example/v1",
            ),
            timeout=10,
            max_retries=4,
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
                "restricted": BinaryQuestion(),
            },
            extra_headers={"X-Test": "value"},
        )
        self.assertDictEqual(
            self.client_cls.call_args.kwargs,
            {
                "api_key": "secret",
                "organization": "org-test",
                "base_url": "https://openai.example/v1",
                "timeout": 10,
                "max_retries": 4,
            },
        )
        self.assertDictEqual(
            self.client.decisions.create.call_args.kwargs,
            {
                "model": "gpt-6-luna",
                "input": "I was charged twice.",
                "questions": [
                    {
                        "type": "predicate",
                        "name": "urgent",
                        "instructions": "Is this urgent?\nTrue: Urgent.",
                    },
                    {
                        "type": "choice",
                        "name": "route",
                        "instructions": "Select a route.",
                        "choices": [
                            {"value": "billing"},
                            {"value": "support"},
                        ],
                    },
                    {
                        "type": "score",
                        "name": "priority",
                        "instructions": "Rate priority.",
                        "levels": [
                            {"label": "low"},
                            {"label": "medium"},
                            {"label": "high"},
                        ],
                    },
                    {
                        "type": "predicate",
                        "name": "restricted",
                        "instructions": "restricted",
                    },
                ],
                "extra_headers": {"X-Test": "value"},
            },
        )
        self.assertDictEqual(
            asdict(response),
            {
                "model": "gpt-6-luna",
                "content": {
                    "urgent": {"type": "binary_answer", "probability": 0.8},
                    "route": {
                        "type": "choice_answer",
                        "choice": "billing",
                        "confidence": 0.9,
                        "probabilities": {"billing": 0.9, "support": 0.1},
                    },
                    "priority": {
                        "type": "score_answer",
                        "score": 1.7,
                        "confidence": 0.85,
                        "legend": {0: "low", 1: "medium", 2: "high"},
                        "probabilities": {0: 0.05, 1: 0.2, 2: 0.75},
                    },
                    "restricted": {"type": "refusal_answer"},
                },
                "usage": {
                    "time": AnyValue(),
                    "input_tokens": 12,
                    "output_tokens": 0,
                    "type": "classifier",
                },
                "id": AnyValue(),
                "created_at": AnyValue(),
                "type": "classifier_response",
                "metadata": {},
            },
        )

    async def test_input_conversion(self) -> None:
        """Convert input and retain criteria when instructions are omitted."""
        model = classifier.OpenAIClassifierModel(self.credential)
        questions = {
            "safe": BinaryQuestion(criteria=BinaryCriteria(false="Unsafe.")),
            "route": ChoiceQuestion(criteria={"billing": "Payments."}),
            "priority": ScoreQuestion(criteria=["low", "high"]),
        }
        for state, expected in [
            ({"text": "你好"}, '{"text": "你好"}'),
            (
                [
                    TextBlock(text="Compare."),
                    DataBlock(
                        source=Base64Source(
                            data="YWJj",
                            media_type="image/png",
                        ),
                    ),
                    DataBlock(
                        source=Base64Source(
                            data="ZGVm",
                            media_type="image/jpeg",
                        ),
                    ),
                ],
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Compare."},
                            {
                                "type": "input_image",
                                "image_url": "data:image/png;base64,YWJj",
                            },
                            {
                                "type": "input_image",
                                "image_url": "data:image/jpeg;base64,ZGVm",
                            },
                        ],
                    },
                ],
            ),
        ]:
            with self.subTest(state=state):
                await model(state, questions)
                self.assertDictEqual(
                    self.client.decisions.create.call_args.kwargs,
                    {
                        "model": "gpt-6-luna",
                        "input": expected,
                        "questions": [
                            {
                                "type": "predicate",
                                "name": "safe",
                                "instructions": "safe\nFalse: Unsafe.",
                            },
                            {
                                "type": "choice",
                                "name": "route",
                                "instructions": "route",
                                "choices": [
                                    {
                                        "value": "billing",
                                        "description": "Payments.",
                                    },
                                ],
                            },
                            {
                                "type": "score",
                                "name": "priority",
                                "instructions": "priority",
                                "levels": [
                                    {"label": "low"},
                                    {"label": "high"},
                                ],
                            },
                        ],
                    },
                )

    async def test_unsupported_media_is_rejected(self) -> None:
        """Do not silently discard unsupported input or fetch URL sources."""
        model = classifier.OpenAIClassifierModel(self.credential)
        for source in [
            Base64Source(data="YWJj", media_type="audio/wav"),
            URLSource(url="https://example.com/a.png", media_type="image/png"),
        ]:
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "Base64Source"):
                    await model(
                        [DataBlock(source=source)],
                        {"safe": BinaryQuestion()},
                    )
        self.client.decisions.create.assert_not_awaited()

    async def test_provider_error_is_raised(self) -> None:
        """Leave retry and error handling to the SDK."""
        error = RuntimeError("failed")
        self.client.decisions.create.side_effect = error
        model = classifier.OpenAIClassifierModel(self.credential)
        with self.assertRaises(RuntimeError) as caught:
            await model("hello", {"safe": BinaryQuestion()})
        self.assertIs(caught.exception, error)

    async def test_old_sdk_has_clear_error(self) -> None:
        """Fail with an upgrade instruction when Decisions is unavailable."""
        self.client_cls.return_value = MagicMock(spec=[])
        with self.assertRaisesRegex(ImportError, "openai>=3.26.0"):
            classifier.OpenAIClassifierModel(self.credential)
