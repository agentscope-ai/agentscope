# -*- coding: utf-8 -*-
"""Tests for the provider-independent classifier model contract."""
from dataclasses import asdict
from typing import Any, Mapping
from unittest import IsolatedAsyncioTestCase, TestCase

from pydantic import ValidationError

from utils import AnyString
from agentscope.classifier import (
    BinaryAnswer,
    BinaryQuestion,
    ChoiceQuestion,
    ClassifierModelBase,
    ClassifierQuestion,
    ClassifierResponse,
    ScoreQuestion,
)
from agentscope.credential import CredentialBase
from agentscope.message import TextBlock


class _MockClassifier(ClassifierModelBase):
    """A minimal classifier used to exercise the base class."""

    def __init__(self) -> None:
        """Initialize the mock classifier."""
        super().__init__(CredentialBase(), "mock-classifier")

    async def __call__(
        self,
        state: str | dict,
        questions: Mapping[str, ClassifierQuestion],
        **kwargs: Any,
    ) -> ClassifierResponse:
        """Return a deterministic response."""
        return ClassifierResponse(
            model=self.model,
            content={"safe": BinaryAnswer(probability=0.75)},
        )


class ClassifierQuestionTest(TestCase):
    """Validate the framework-owned question types."""

    def test_question_models(self) -> None:
        """Question models should preserve their complete typed structure."""
        choice = ChoiceQuestion(
            instructions="Select a route.",
            criteria={"billing": None, "support": "Technical support."},
        )
        score = ScoreQuestion(
            instructions="Rate urgency.",
            criteria=["Can wait.", "Handle today."],
        )

        self.assertDictEqual(
            choice.model_dump(),
            {
                "type": "choice",
                "criteria": {
                    "billing": None,
                    "support": "Technical support.",
                },
                "instructions": "Select a route.",
            },
        )
        self.assertDictEqual(
            score.model_dump(),
            {
                "type": "score",
                "criteria": ["Can wait.", "Handle today."],
                "instructions": "Rate urgency.",
            },
        )

    def test_empty_criteria_are_rejected(self) -> None:
        """Choice and score questions require at least one criterion."""
        with self.assertRaises(ValidationError):
            ChoiceQuestion(criteria={})
        with self.assertRaises(ValidationError):
            ScoreQuestion(criteria=[])

    def test_non_string_instructions_are_rejected(self) -> None:
        """Question instructions must be plain text."""
        with self.assertRaises(ValidationError):
            BinaryQuestion(instructions=TextBlock(text="hello"))


class ClassifierModelBaseTest(IsolatedAsyncioTestCase):
    """Test the classifier model contract."""

    async def test_call(self) -> None:
        """A call returns the typed answers keyed by question name."""
        response = await _MockClassifier()(
            state="hello",
            questions={"safe": ChoiceQuestion(criteria={"yes": None})},
        )

        self.assertDictEqual(
            asdict(response),
            {
                "model": "mock-classifier",
                "content": {
                    "safe": {
                        "probability": 0.75,
                        "type": "binary_answer",
                    },
                },
                "usage": None,
                "id": AnyString(),
                "created_at": AnyString(),
                "type": "classifier_response",
                "metadata": {},
            },
        )
