# -*- coding: utf-8 -*-
"""Tests for TTS model release dates and API serialization."""
import tempfile
from datetime import date
from pathlib import Path
from unittest import TestCase

from pydantic import BaseModel, ValidationError

from agentscope.app._router._schema._tts_model import ListTTSModelsResponse
from agentscope.tts import TTSModelCard


class _Parameters(BaseModel):
    """Minimal parameters for testing model metadata independently."""


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
