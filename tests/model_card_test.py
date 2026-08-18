# -*- coding: utf-8 -*-
"""Unit tests for model card parsing from YAML files."""
import os
import tempfile
import unittest

from pydantic import BaseModel

from agentscope.model._model_card import ModelCard
from agentscope.tts._tts_model_card import TTSModelCard


class _Params(BaseModel):
    """Minimal parameter class for model card tests."""

    max_tokens: int | None = None
    temperature: float | None = None


class _TTSParams(BaseModel):
    """Minimal TTS parameter class for model card tests."""

    voice: str | None = None
    speed: float | None = None


def _write_yaml(content: str) -> str:
    """Write YAML to a temp file and return its path."""
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".yaml",
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(content)
        return f.name


class ModelCardFromYamlTest(unittest.TestCase):
    """Tests for ModelCard.from_yaml."""

    def test_null_override_removes_parameter(self) -> None:
        """A null parameter override removes the parameter without
        breaking the card."""
        path = _write_yaml(
            "name: test-model\n"
            "label: Test Model\n"
            "status: active\n"
            "context_size: 128000\n"
            "output_size: 4096\n"
            "parameter_overrides:\n"
            "  max_tokens: null\n",
        )
        try:
            card = ModelCard.from_yaml(path, _Params)
        finally:
            os.unlink(path)

        self.assertNotIn("max_tokens", card.parameter_schema["properties"])
        self.assertEqual(
            card.parameters_overrides,
            {},
        )

    def test_null_override_mixed_with_dict_overrides(self) -> None:
        """Null overrides are dropped from the stored overrides while
        dict overrides are kept."""
        path = _write_yaml(
            "name: test-model\n"
            "label: Test Model\n"
            "status: active\n"
            "context_size: 128000\n"
            "output_size: 4096\n"
            "parameter_overrides:\n"
            "  max_tokens: null\n"
            "  temperature:\n"
            "    default: 0.7\n",
        )
        try:
            card = ModelCard.from_yaml(path, _Params)
        finally:
            os.unlink(path)

        self.assertNotIn("max_tokens", card.parameter_schema["properties"])
        self.assertEqual(
            card.parameter_schema["properties"]["temperature"]["default"],
            0.7,
        )
        self.assertEqual(
            card.parameters_overrides,
            {"temperature": {"default": 0.7}},
        )


class TTSModelCardFromYamlTest(unittest.TestCase):
    """Tests for TTSModelCard.from_yaml."""

    def test_null_override_removes_parameter(self) -> None:
        """A null parameter override removes the parameter without
        breaking the card."""
        path = _write_yaml(
            "name: test-tts\n"
            "label: Test TTS\n"
            "parameter_overrides:\n"
            "  voice: null\n",
        )
        try:
            card = TTSModelCard.from_yaml(path, _TTSParams)
        finally:
            os.unlink(path)

        self.assertNotIn("voice", card.parameter_schema["properties"])
        self.assertEqual(
            card.parameters_overrides,
            {},
        )
