# -*- coding: utf-8 -*-
"""Tests for chat and TTS model card metadata and serialization."""
import tempfile
from datetime import date
from pathlib import Path
from unittest import TestCase

import yaml
from pydantic import BaseModel, ValidationError

from agentscope.app._router._schema._tts_model import ListTTSModelsResponse
from agentscope.credential import OpenAICredential
from agentscope.model import (
    AnthropicChatModel,
    OpenAIChatModel,
    OpenAIResponseModel,
    XAIChatModel,
)
from agentscope.tts import TTSModelCard


class _Parameters(BaseModel):
    """Minimal parameters for testing model metadata independently."""


class ModelCardTest(TestCase):
    """Verify model card loading, serialization, and parameter filtering."""

    def test_release_date_loading_and_serialization(self) -> None:
        """Load optional YAML dates and serialize them as ISO date strings."""
        for field, expected_date in (
            ("", None),
            ("release_date: null\n", None),
            ("release_date: 2026-11-12\n", date(2026, 11, 12)),
            ('release_date: "2026-11-12"\n', date(2026, 11, 12)),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "custom.yaml"
                    path.write_text(
                        "name: custom-model\n"
                        "label: Custom model\n"
                        "context_size: 1000\n"
                        f"output_size: 100\n{field}",
                        encoding="utf-8",
                    )
                    cards = OpenAIResponseModel.list_models(directory)
                self.assertEqual(
                    [
                        {
                            "name": card.name,
                            "release_date": card.release_date,
                            "json": card.model_dump(
                                mode="json",
                                include={"release_date"},
                            ),
                        }
                        for card in cards
                    ],
                    [
                        {
                            "name": "custom-model",
                            "release_date": expected_date,
                            "json": {
                                "release_date": (
                                    expected_date.isoformat()
                                    if expected_date
                                    else None
                                ),
                            },
                        },
                    ],
                )

    def test_builtin_openai_labels(self) -> None:
        """The two GPT-4o implementations have distinct display labels."""
        self.assertListEqual(
            [
                (card.model_class, card.label)
                for card in OpenAICredential.list_models()
                if card.name == "gpt-4o"
            ],
            [
                ("openai_chat", "GPT-4o"),
                ("openai_response", "GPT-4o (Responses API)"),
            ],
        )

    def test_openai_effort_visible_without_thinking_toggle(self) -> None:
        """Expose effort independently of the hidden thinking toggle."""
        expected = {
            "gpt-6-sol": ["reasoning_effort"],
            "gpt-6-luna": ["reasoning_effort"],
            "gpt-6.1-sol": ["reasoning_effort"],
            "gpt-4o": [],
        }
        for model_cls in (OpenAIChatModel, OpenAIResponseModel):
            with self.subTest(model_cls=model_cls.__name__):
                self.assertEqual(
                    {
                        card.name: [
                            key
                            for key in ("thinking_enable", "reasoning_effort")
                            if key in card.parameter_schema["properties"]
                        ]
                        for card in model_cls.list_models()
                        if card.name in expected
                    },
                    expected,
                )

    def test_effort_edit_metadata_survives_card_filtering(self) -> None:
        """Cards preserve edit rules declared by each adapter."""
        for model_cls, expected_resets in (
            (OpenAIChatModel, {"thinking_enable": None}),
            (OpenAIResponseModel, {"thinking_enable": None}),
            (AnthropicChatModel, None),
            (XAIChatModel, None),
        ):
            for output_types, overrides, toggle_visible in (
                (["text/plain"], {}, False),
                (["text/plain", "application/x-thinking"], {}, True),
                (
                    ["text/plain", "application/x-thinking"],
                    {"thinking_enable": {"hidden": True}},
                    False,
                ),
            ):
                with self.subTest(
                    model_cls=model_cls.__name__,
                    output_types=output_types,
                    overrides=overrides,
                ):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "custom.yaml"
                        path.write_text(
                            yaml.safe_dump(
                                {
                                    "name": "custom-model",
                                    "label": "Custom model",
                                    "context_size": 1000,
                                    "output_size": 100,
                                    "output_types": output_types,
                                    "parameter_overrides": overrides,
                                },
                            ),
                            encoding="utf-8",
                        )
                        card = model_cls.list_models(directory)[0]
                    properties = card.parameter_schema["properties"]
                    self.assertEqual(
                        {
                            "toggle_visible": "thinking_enable" in properties,
                            "effort_edit_resets": properties[
                                "reasoning_effort"
                            ].get("x-reset-hidden-on-change"),
                        },
                        {
                            "toggle_visible": toggle_visible,
                            "effort_edit_resets": expected_resets,
                        },
                    )

    def test_custom_labels_are_preserved(self) -> None:
        """Custom YAML labels remain unchanged, with or without a suffix."""
        for label in ("My deployment", "My deployment (Responses API)"):
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "custom.yaml"
                    path.write_text(
                        yaml.safe_dump(
                            {
                                "name": "custom-model",
                                "label": label,
                                "context_size": 1000,
                                "output_size": 100,
                            },
                        ),
                        encoding="utf-8",
                    )
                    cards = OpenAIResponseModel.list_models(directory)
                self.assertListEqual(
                    [
                        (card.name, card.model_class, card.label)
                        for card in cards
                    ],
                    [("custom-model", "openai_response", label)],
                )


class TTSModelCardTest(TestCase):
    """Verify optional dates without changing existing model metadata."""

    def test_release_date_loading_and_serialization(self) -> None:
        """Load YAML dates and expose ISO dates or null in API responses."""
        for field, expected_date in (
            ("", None),
            ("release_date: null\n", None),
            ("release_date: 2026-01-23\n", date(2026, 1, 23)),
            ('release_date: "2026-01-23"\n', date(2026, 1, 23)),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "custom.yaml"
                    path.write_text(
                        f"name: custom-tts\nlabel: Custom TTS\n{field}",
                        encoding="utf-8",
                    )
                    card = TTSModelCard.from_yaml(str(path), _Parameters)

                expected = {
                    "type": "tts_model",
                    "name": "custom-tts",
                    "label": "Custom TTS",
                    "status": "active",
                    "deprecated_at": None,
                    "release_date": expected_date,
                    "input_types": ["text/plain"],
                    "output_types": ["audio/wav"],
                    "realtime": False,
                    "voice_cloning": False,
                    "parameter_schema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                    "parameters_overrides": {},
                }
                self.assertEqual(card.model_dump(), expected)
                response = ListTTSModelsResponse(models=[card], total=1)
                self.assertEqual(
                    response.model_dump(mode="json"),
                    {
                        "models": [
                            {
                                **expected,
                                "release_date": (
                                    expected_date.isoformat()
                                    if expected_date
                                    else None
                                ),
                            },
                        ],
                        "total": 1,
                    },
                )

    def test_invalid_release_date_is_rejected(self) -> None:
        """Reject malformed dates instead of silently losing metadata."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom.yaml"
            path.write_text(
                "name: custom-tts\n"
                "label: Custom TTS\n"
                'release_date: "2026-02-30"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValidationError) as raised:
                TTSModelCard.from_yaml(str(path), _Parameters)
        self.assertEqual(
            [error["loc"] for error in raised.exception.errors()],
            [("release_date",)],
        )
