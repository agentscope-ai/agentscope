# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for Voice Profile storage model and constants.

Covers:
  * VoiceProfileData and VoiceProfileRecord model creation.
  * ENGINE_TO_CREDENTIAL_TYPE mapping completeness.
  * ENGINE_VOICE_CLONING mapping.
"""
import typing
from unittest import TestCase

from agentscope.app.storage._model import (
    ENGINE_TO_CREDENTIAL_TYPE,
    VoiceProfileData,
    VoiceProfileRecord,
)
from agentscope.app.storage._model._voice_profile import (
    _ENGINE_TYPE,
    ENGINE_SOURCE,
    ENGINE_GPU_REQUIREMENT,
    ENGINE_VOICE_CLONING,
)


class TestVoiceProfileModel(TestCase):
    """Unit tests for VoiceProfileData and VoiceProfileRecord."""

    def test_create_voice_profile_data_minimal(self) -> None:
        """VoiceProfileData can be created with only name."""
        data = VoiceProfileData(name="Test Voice")
        self.assertEqual(
            data.model_dump(),
            {
                "name": "Test Voice",
                "engine": None,
                "model": None,
                "credential_id": None,
                "source": None,
                "voice": None,
                "metadata": None,
            },
        )

    def test_create_voice_profile_data_full(self) -> None:
        """VoiceProfileData can be created with all fields."""
        data = VoiceProfileData(
            name="Clone Voice",
            engine="dashscope_tts",
            model="qwen3-tts-flash",
            credential_id="cred-abc-123",
            source="api",
            voice="cosyvoice-clone-abc123",
            metadata={"quality": "high"},
        )
        self.assertEqual(
            data.model_dump(),
            {
                "name": "Clone Voice",
                "engine": "dashscope_tts",
                "model": "qwen3-tts-flash",
                "credential_id": "cred-abc-123",
                "source": "api",
                "voice": "cosyvoice-clone-abc123",
                "metadata": {"quality": "high"},
            },
        )

    def test_create_voice_profile_record(self) -> None:
        """VoiceProfileRecord can be created with auto id."""
        data = VoiceProfileData(name="Test")
        record = VoiceProfileRecord(
            user_id="user-1",
            data=data,
        )
        self.assertEqual(record.user_id, "user-1")
        self.assertEqual(record.data.name, "Test")
        self.assertIsNotNone(record.id)
        self.assertIsNotNone(record.created_at)


class TestEngineToCredentialMapping(TestCase):
    """Unit tests for ENGINE_TO_CREDENTIAL_TYPE mapping."""

    def test_api_engines_map_correctly(self) -> None:
        """All engines map to their respective credentials."""
        expected = {
            "cosyvoice": "dashscope_credential",
            "dashscope_tts": "dashscope_credential",
            "openai_tts": "openai_credential",
            "gemini_tts": "gemini_credential",
        }
        self.assertEqual(ENGINE_TO_CREDENTIAL_TYPE, expected)

    def test_all_engines_have_mapping(self) -> None:
        """Every _ENGINE_TYPE value has a credential mapping."""
        args = typing.get_args(_ENGINE_TYPE)
        for engine in args:
            self.assertIn(
                engine,
                ENGINE_TO_CREDENTIAL_TYPE,
                f"Missing mapping for engine '{engine}'",
            )


class TestEngineConstants(TestCase):
    """Unit tests for engine-level constant dicts."""

    def test_all_engines_have_source(self) -> None:
        """Every engine type has an ENGINE_SOURCE entry."""
        args = typing.get_args(_ENGINE_TYPE)
        for engine in args:
            self.assertIn(engine, ENGINE_SOURCE)

    def test_all_engines_have_gpu_requirement(self) -> None:
        """Every engine type has an ENGINE_GPU_REQUIREMENT."""
        args = typing.get_args(_ENGINE_TYPE)
        for engine in args:
            self.assertIn(engine, ENGINE_GPU_REQUIREMENT)

    def test_all_engines_have_voice_cloning_flag(self) -> None:
        """Every engine type has an ENGINE_VOICE_CLONING."""
        args = typing.get_args(_ENGINE_TYPE)
        for engine in args:
            self.assertIn(engine, ENGINE_VOICE_CLONING)

    def test_api_engines_no_gpu(self) -> None:
        """API engines should not require GPU."""
        expected = {
            "cosyvoice": None,
            "dashscope_tts": None,
            "openai_tts": None,
            "gemini_tts": None,
        }
        self.assertEqual(ENGINE_GPU_REQUIREMENT, expected)

    def test_voice_cloning_flags(self) -> None:
        """Verify known cloning support flags."""
        expected = {
            "cosyvoice": True,
            "dashscope_tts": True,
            "openai_tts": True,
            "gemini_tts": False,
        }
        self.assertEqual(ENGINE_VOICE_CLONING, expected)
