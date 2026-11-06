# -*- coding: utf-8 -*-
"""Regression tests for model-card parameter schema references."""
import copy
from enum import Enum
from unittest import TestCase
from unittest.mock import mock_open, patch

from jsonschema import Draft202012Validator, ValidationError
from pydantic import BaseModel, Field

from agentscope.embedding import EmbeddingModelCard
from agentscope.model import ModelCard
from agentscope.tts import TTSModelCard


class _Mode(str, Enum):
    """Example parameter with a referenced enum definition."""

    FAST = "fast"
    ACCURATE = "accurate"


class _Options(BaseModel):
    """Nested options with a validation constraint."""

    limit: int = Field(ge=1)


class _Parameters(BaseModel):
    """Parameters containing both nested and enum references."""

    options: _Options
    mode: _Mode


_CARD_CLASSES = (ModelCard, EmbeddingModelCard, TTSModelCard)
_CONFIG = """name: test-model
label: Test Model
context_size: 4096
output_size: 1024
dimensions: 128
parameter_overrides:
  mode:
    default: fast
"""


class ModelCardSchemaTest(TestCase):
    """Exercise schema validation across the three model-card loaders."""

    def test_referenced_parameters_validate(self) -> None:
        """Resolve definitions without losing nested or enum constraints."""
        for card_class in _CARD_CLASSES:
            with self.subTest(card=card_class.__name__):
                with patch("builtins.open", mock_open(read_data=_CONFIG)):
                    card = card_class.from_yaml("model.yaml", _Parameters)
                validator = Draft202012Validator(card.parameter_schema)
                validator.validate({"options": {"limit": 1}, "mode": "fast"})
                for invalid in (
                    {"options": {"limit": 0}, "mode": "fast"},
                    {"options": {"limit": 1}, "mode": "unknown"},
                ):
                    with self.assertRaises(ValidationError):
                        validator.validate(invalid)
                self.assertEqual(
                    card.parameter_schema["properties"]["mode"]["default"],
                    "fast",
                )

    def test_definitions_are_independent(self) -> None:
        """Keep card definitions independent of the source parameter schema."""
        for card_class in _CARD_CLASSES:
            with self.subTest(card=card_class.__name__):
                source_schema = _Parameters.model_json_schema()
                original = copy.deepcopy(source_schema)
                with (
                    patch("builtins.open", mock_open(read_data=_CONFIG)),
                    patch.object(
                        _Parameters,
                        "model_json_schema",
                        return_value=source_schema,
                    ),
                ):
                    card = card_class.from_yaml("model.yaml", _Parameters)
                self.assertEqual(
                    card.parameter_schema["$defs"],
                    original["$defs"],
                )
                card.parameter_schema["$defs"]["_Mode"]["enum"].append("other")
                self.assertEqual(source_schema, original)

    def test_schema_without_definitions(self) -> None:
        """Keep the existing schema shape when there are no references."""

        class Parameters(BaseModel):
            """Plain scalar parameter."""

            limit: int

        for card_class in _CARD_CLASSES:
            with self.subTest(card=card_class.__name__):
                with patch("builtins.open", mock_open(read_data=_CONFIG)):
                    card = card_class.from_yaml("model.yaml", Parameters)
                self.assertEqual(
                    card.parameter_schema,
                    {
                        "type": "object",
                        "properties": Parameters.model_json_schema()[
                            "properties"
                        ],
                        "required": ["limit"],
                    },
                )
