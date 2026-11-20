# -*- coding: utf-8 -*-
# pylint: disable=missing-class-docstring,missing-function-docstring
"""Unit tests for embedding model-card parameter schemas."""
from unittest import TestCase
from unittest.mock import mock_open, patch

from pydantic import BaseModel, Field

from agentscope.embedding import EmbeddingModelCard


class _Parameters(BaseModel):
    """Parameters with one hidden and one configurable required field."""

    internal: str = Field(..., description="Service-managed parameter.")
    batch_size: int = Field(default=8, description="Configurable parameter.")


class EmbeddingModelCardTest(TestCase):
    """Apply parameter overrides consistently to properties and required."""

    def test_hidden_required_parameter_is_removed_from_required(
        self,
    ) -> None:
        """A hidden required parameter must not remain in the schema."""
        yaml_text = (
            "name: custom\n"
            "label: Custom\n"
            "dimensions: 8\n"
            "parameter_overrides:\n"
            "  internal:\n"
            "    hidden: true\n"
        )
        with patch("builtins.open", mock_open(read_data=yaml_text)):
            card = EmbeddingModelCard.from_yaml("model.yaml", _Parameters)

        self.assertNotIn("internal", card.parameter_schema["properties"])
        self.assertEqual(card.parameter_schema["required"], [])
