# -*- coding: utf-8 -*-
"""Tests for realtime model card loading."""
import tempfile
from pathlib import Path
from unittest import TestCase

from pydantic import BaseModel, Field

from agentscope.realtime import RealtimeModelCard


class _Parameters(BaseModel):
    """Minimal parameters for exercising card filtering."""

    hidden: str = Field(description="Removed by the card override.")
    visible: int = Field(default=1, description="Kept in the card schema.")


class RealtimeModelCardTest(TestCase):
    """Verify realtime card loading and parameter overlay handling."""

    def test_null_override_removes_parameter_without_validation_error(
        self,
    ) -> None:
        """A null override deletes a parameter instead of failing the card."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "realtime-card.yaml")
            path.write_text(
                "name: test-model\n"
                "label: Test Model\n"
                "parameter_overrides:\n"
                "  hidden: null\n",
                encoding="utf-8",
            )

            card = RealtimeModelCard.from_yaml(path, _Parameters)

            self.assertNotIn("hidden", card.parameter_schema["properties"])
            self.assertIn("visible", card.parameter_schema["properties"])
            self.assertEqual(card.parameter_overrides, {})
            self.assertEqual(
                RealtimeModelCard.list_from_directory(tmp, _Parameters),
                [card],
            )
