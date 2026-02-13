# -*- coding: utf-8 -*-
"""Unittests for the message converter functionality in AgentScope tracing."""
from typing import Any, Dict
from unittest import TestCase

from agentscope.message import (
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
)
from agentscope.tracing._converter import _convert_block_to_part


class ConverterTest(TestCase):
    """Test cases for _convert_block_to_part converter"""

    def test_convert_text_block(self) -> None:
        """Test text block conversion"""
        # Normal text block
        block: TextBlock = {
            "type": "text",
            "text": "Hello, world!",
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "text")
        self.assertEqual(result["content"], "Hello, world!")

        # Missing text field
        block = {"type": "text"}
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "text")
        self.assertEqual(result["content"], "")

        # Empty text
        block = {"type": "text", "text": ""}
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "text")
        self.assertEqual(result["content"], "")

    def test_convert_thinking_block(self) -> None:
        """Test thinking block conversion"""
        # Normal thinking block
        block: Dict[str, Any] = {
            "type": "thinking",
            "thinking": "Let me think about this...",
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "reasoning")
        self.assertEqual(result["content"], "Let me think about this...")

        # Missing thinking field
        block = {"type": "thinking"}
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "reasoning")
        self.assertEqual(result["content"], "")

    def test_convert_tool_use_block(self) -> None:
        """Test tool_use block conversion"""
        # Normal tool_use block
        block: ToolUseBlock = {
            "type": "tool_use",
            "id": "call_123",
            "name": "search",
            "input": {"query": "test query", "limit": 10},
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "tool_call")
        self.assertEqual(result["id"], "call_123")
        self.assertEqual(result["name"], "search")
        self.assertEqual(
            result["arguments"],
            {"query": "test query", "limit": 10},
        )

        # Missing fields
        block = {
            "type": "tool_use",
            "id": "",
            "name": "",
            "input": {},
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "tool_call")
        self.assertEqual(result["id"], "")
        self.assertEqual(result["name"], "")
        self.assertEqual(result["arguments"], {})

    def test_convert_tool_result_block(self) -> None:
        """Test tool_result block conversion"""
        # String output
        block: ToolResultBlock = {
            "type": "tool_result",
            "id": "call_123",
            "name": "search",
            "output": "Search results here",
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "tool_call_response")
        self.assertEqual(result["id"], "call_123")
        self.assertEqual(result["response"], "Search results here")

        # Dict output
        block = {
            "type": "tool_result",
            "id": "call_123",
            "name": "search",
            "output": {"result": "success", "data": [1, 2, 3]},
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertIsInstance(result["response"], str)
        self.assertIn("result", result["response"])
        self.assertIn("success", result["response"])

        # List output
        block = {
            "type": "tool_result",
            "id": "call_123",
            "name": "search",
            "output": [
                {"type": "text", "text": "Result 1"},
                {"type": "text", "text": "Result 2"},
            ],
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertIsInstance(result["response"], str)
        self.assertIn("Result 1", result["response"])

        # Numeric output
        block = {
            "type": "tool_result",
            "id": "call_123",
            "name": "calculate",
            "output": 42,
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["response"], "42")

        # Empty output
        block = {
            "type": "tool_result",
            "id": "call_123",
            "name": "search",
            "output": "",
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["response"], "")

        # None output
        block = {
            "type": "tool_result",
            "id": "call_123",
            "name": "search",
            "output": None,
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["response"], "None")

        # Missing id
        block = {
            "type": "tool_result",
            "id": "",
            "name": "search",
            "output": "result",
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "")

    def test_convert_image_block(self) -> None:
        """Test image block conversion"""
        # URL source
        source: Dict[str, Any] = {
            "type": "url",
            "url": "https://example.com/image.jpg",
        }
        block: ImageBlock = {
            "type": "image",
            "source": source,
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "uri")
        self.assertEqual(result["uri"], "https://example.com/image.jpg")
        self.assertEqual(result["modality"], "image")

        # Base64 source
        source = {
            "type": "base64",
            "media_type": "image/png",
            "data": "base",
        }
        block = {
            "type": "image",
            "source": source,
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "blob")
        self.assertEqual(result["content"], source["data"])
        self.assertEqual(result["media_type"], "image/png")
        self.assertEqual(result["modality"], "image")

        # Base64 with default media_type
        source = {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": "base64data",
        }
        block = {
            "type": "image",
            "source": source,
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["media_type"], "image/jpeg")

        # URL missing url field
        block = {
            "type": "image",
            "source": {"type": "url"},
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["uri"], "")

        # Base64 missing fields
        block = {
            "type": "image",
            "source": {"type": "base64"},
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "")
        self.assertEqual(result["media_type"], "image/jpeg")

        # Invalid source type
        block = {
            "type": "image",
            "source": {"type": "invalid"},
        }
        result = _convert_block_to_part(block)
        self.assertIsNone(result)

        # Missing source
        block = {"type": "image"}
        result = _convert_block_to_part(block)
        self.assertIsNone(result)

    def test_convert_audio_block(self) -> None:
        """Test audio block conversion"""
        # URL source
        source: Dict[str, Any] = {
            "type": "url",
            "url": "https://example.com/audio.wav",
        }
        block: AudioBlock = {
            "type": "audio",
            "source": source,
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "uri")
        self.assertEqual(result["uri"], "https://example.com/audio.wav")
        self.assertEqual(result["modality"], "audio")

        # Base64 source
        source = {
            "type": "base64",
            "media_type": "audio/mpeg",
            "data": "base64audiodata",
        }
        block = {
            "type": "audio",
            "source": source,
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "blob")
        self.assertEqual(result["content"], "base64audiodata")
        self.assertEqual(result["media_type"], "audio/mpeg")
        self.assertEqual(result["modality"], "audio")

        # Base64 with default media_type
        source = {
            "type": "base64",
            "media_type": "audio/wav",
            "data": "base64data",
        }
        block = {
            "type": "audio",
            "source": source,
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["media_type"], "audio/wav")

        # URL missing url field
        block = {
            "type": "audio",
            "source": {"type": "url"},
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["uri"], "")

        # Base64 missing fields
        block = {
            "type": "audio",
            "source": {"type": "base64"},
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "")
        self.assertEqual(result["media_type"], "audio/wav")

        # Invalid source type
        block = {
            "type": "audio",
            "source": {"type": "invalid"},
        }
        result = _convert_block_to_part(block)
        self.assertIsNone(result)

    def test_convert_video_block(self) -> None:
        """Test video block conversion"""
        # URL source
        source: Dict[str, Any] = {
            "type": "url",
            "url": "https://example.com/video.mp4",
        }
        block: VideoBlock = {
            "type": "video",
            "source": source,
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "uri")
        self.assertEqual(result["uri"], "https://example.com/video.mp4")
        self.assertEqual(result["modality"], "video")

        # Base64 source
        source = {
            "type": "base64",
            "media_type": "video/webm",
            "data": "base64videodata",
        }
        block = {
            "type": "video",
            "source": source,
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "blob")
        self.assertEqual(result["content"], "base64videodata")
        self.assertEqual(result["media_type"], "video/webm")
        self.assertEqual(result["modality"], "video")

        # Base64 with default media_type
        source = {
            "type": "base64",
            "media_type": "video/mp4",
            "data": "base64data",
        }
        block = {
            "type": "video",
            "source": source,
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["media_type"], "video/mp4")

        # URL missing url field
        block = {
            "type": "video",
            "source": {"type": "url"},
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["uri"], "")

        # Base64 missing fields
        block = {
            "type": "video",
            "source": {"type": "base64"},
        }
        result = _convert_block_to_part(block)
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "")
        self.assertEqual(result["media_type"], "video/mp4")

        # Invalid source type
        block = {
            "type": "video",
            "source": {"type": "invalid"},
        }
        result = _convert_block_to_part(block)
        self.assertIsNone(result)

    def test_convert_invalid_blocks(self) -> None:
        """Test invalid block types"""
        # Unknown block type
        block = {"type": "unknown_type"}
        result = _convert_block_to_part(block)
        self.assertIsNone(result)

        # Missing type field
        block = {}
        result = _convert_block_to_part(block)
        self.assertIsNone(result)
