# -*- coding: utf-8 -*-
"""Regression tests for ModelCard.from_yaml parameter-schema merging."""
from unittest import TestCase
from unittest.mock import mock_open, patch

from pydantic import BaseModel, Field

from agentscope.model._model_card import ModelCard


class _Params(BaseModel):
    """A parameter class with one required and one optional field."""

    temperature: float = Field(ge=0, le=2)
    top_p: float = 0.9


_BASE_YAML = """name: m
label: M
status: active
context_size: 1000
output_size: 100
"""

_HIDE_TEMPERATURE = """parameter_overrides:
  temperature:
    hidden: true
"""

_HIDE_BOTH = """parameter_overrides:
  temperature:
    hidden: true
  top_p:
    hidden: true
"""


class ModelCardRequiredTest(TestCase):
    """``required`` must only name properties that survived filtering."""

    def _card(self, extra: str = "") -> ModelCard:
        with patch(
            "builtins.open",
            mock_open(read_data=_BASE_YAML + extra),
        ):
            return ModelCard.from_yaml("model.yaml", _Params)

    def test_no_override_keeps_required_field(self) -> None:
        """Control: with no override nothing is filtered out."""
        schema = self._card().parameter_schema
        self.assertEqual(schema["required"], ["temperature"])
        self.assertIn("temperature", schema["properties"])
        self.assertIn("top_p", schema["properties"])

    def test_hidden_required_property_dropped_from_required(self) -> None:
        """Hiding a required property must also drop it from ``required``."""
        schema = self._card(_HIDE_TEMPERATURE).parameter_schema
        self.assertNotIn("temperature", schema["properties"])
        self.assertNotIn("temperature", schema["required"])
        self.assertIn("top_p", schema["properties"])

    def test_all_hidden_properties_leave_empty_required(self) -> None:
        """When every property is hidden, ``required`` must be empty."""
        schema = self._card(_HIDE_BOTH).parameter_schema
        self.assertEqual(schema["properties"], {})
        self.assertEqual(schema["required"], [])

    def test_overrides_do_not_mutate_parameter_class(self) -> None:
        """Control: overrides must not leak into the class-level schema."""
        self._card(_HIDE_TEMPERATURE)
        self.assertIn(
            "temperature",
            _Params.model_json_schema().get("required", []),
        )
