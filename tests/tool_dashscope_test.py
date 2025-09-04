# -*- coding: utf-8 -*-
"""Unit tests for DashScope tools"""

import base64
from unittest.mock import Mock, patch, MagicMock

from agentscope.message import ImageBlock, TextBlock, AudioBlock
from agentscope.tool import ToolResponse
from agentscope.tool import (
    dashscope_text_to_image,
    dashscope_image_to_text,
    dashscope_text_to_audio,
)


class TestDashScopeTextToImage:
    """Test cases for dashscope_text_to_image function"""

    def test_text_to_image_success_url_mode(self) -> None:
        """Test successful image generation in URL mode"""
        mock_dashscope = MagicMock()
        mock_dashscope.ImageSynthesis.call.return_value.output = {
            "results": [
                {"url": "https://example.com/image1.jpg"},
                {"url": "https://example.com/image2.png"},
            ],
        }

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_text_to_image(
                prompt="A beautiful landscape",
                api_key="test_key",
                n=2,
                use_base64=False,
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            ImageBlock(
                type="image",
                source={
                    "type": "url",
                    "url": "https://example.com/image1.jpg",
                },
            ),
            ImageBlock(
                type="image",
                source={
                    "type": "url",
                    "url": "https://example.com/image2.png",
                },
            ),
        ]

    def test_text_to_image_success_base64_mode(self) -> None:
        """Test successful image generation in base64 mode"""
        fake_image_data = b"fake_image_data"
        expected_base64 = base64.b64encode(fake_image_data).decode("utf-8")

        mock_dashscope = MagicMock()
        mock_dashscope.ImageSynthesis.call.return_value.output = {
            "results": [{"url": "https://example.com/image1.jpg"}],
        }

        with (
            patch.dict(
                "sys.modules",
                {"dashscope": mock_dashscope},
            ),
            patch(
                "agentscope.tool._multi_modality._dashscope_tools."
                "_get_bytes_from_web_url",
            ) as mock_get_bytes,
        ):
            mock_get_bytes.return_value = expected_base64
            result = dashscope_text_to_image(
                prompt="A beautiful landscape",
                api_key="test_key",
                n=1,
                use_base64=True,
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            ImageBlock(
                type="image",
                source={
                    "type": "base64",
                    "media_type": "image/jpg",
                    "data": expected_base64,
                },
            ),
        ]

    def test_text_to_image_empty_results(self) -> None:
        """Test when empty results are returned"""
        mock_dashscope = MagicMock()
        mock_dashscope.ImageSynthesis.call.return_value.output = {
            "results": [],
        }

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_text_to_image(
                prompt="A beautiful landscape",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert len(result.content) == 0

    def test_text_to_image_none_results(self) -> None:
        """Test when None results are returned"""
        mock_dashscope = MagicMock()
        mock_dashscope.ImageSynthesis.call.return_value.output = {}

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_text_to_image(
                prompt="A beautiful landscape",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Failed to generate images: 'results'",
            ),
        ]

    def test_text_to_image_exception(self) -> None:
        """Test exception handling"""
        mock_dashscope = MagicMock()
        mock_dashscope.ImageSynthesis.call.side_effect = Exception("API Error")

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_text_to_image(
                prompt="A beautiful landscape",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Failed to generate images: API Error",
            ),
        ]


class TestDashScopeImageToText:
    """Test cases for dashscope_image_to_text function"""

    def test_image_to_text_single_url_success(self) -> None:
        """Test successful processing of single image URL"""
        mock_dashscope = MagicMock()
        mock_dashscope.MultiModalConversation.call.return_value.output = {
            "choices": [
                {"message": {"content": "This is a beautiful landscape"}},
            ],
        }

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_image_to_text(
                image_urls="https://example.com/image.jpg",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="This is a beautiful landscape",
            ),
        ]

    def test_image_to_text_multiple_urls_success(self) -> None:
        """Test successful processing of multiple image URLs"""
        mock_dashscope = MagicMock()
        mock_dashscope.MultiModalConversation.call.return_value.output = {
            "choices": [
                {"message": {"content": "Multiple images description"}},
            ],
        }

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_image_to_text(
                image_urls=[
                    "https://example.com/1.jpg",
                    "https://example.com/2.jpg",
                ],
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Multiple images description",
            ),
        ]

    def test_image_to_text_local_file_success(self) -> None:
        """Test successful processing of local file"""
        mock_dashscope = MagicMock()
        mock_dashscope.MultiModalConversation.call.return_value.output = {
            "choices": [{"message": {"content": "Local image description"}}],
        }

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}), patch(
            "agentscope.tool._multi_modality._dashscope_tools.os.path.exists",
        ) as mock_exists, patch(
            "agentscope.tool._multi_modality._dashscope_tools.os.path.isfile",
        ) as mock_isfile, patch(
            "agentscope.tool._multi_modality._dashscope_tools.os.path.abspath",
        ) as mock_abspath:
            mock_exists.return_value = True
            mock_isfile.return_value = True
            mock_abspath.return_value = "/absolute/path/to/image.jpg"

            result = dashscope_image_to_text(
                image_urls="./local_image.jpg",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Local image description",
            ),
        ]

    def test_image_to_text_invalid_local_path(self) -> None:
        """Test error handling for invalid local path"""
        with patch(
            "agentscope.tool._multi_modality._dashscope_tools.os.path.exists",
        ) as mock_exists, patch(
            "agentscope.tool._multi_modality._dashscope_tools.os.path.isfile",
        ) as mock_isfile:
            mock_exists.return_value = True
            mock_isfile.return_value = False

            result = dashscope_image_to_text(
                image_urls="./invalid_path",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text='Error: The input image url "./invalid_path" is not a '
                "file.",
            ),
        ]

    def test_image_to_text_list_content_response(self) -> None:
        """Test handling of list content in response"""
        mock_dashscope = MagicMock()
        mock_dashscope.MultiModalConversation.call.return_value.output = {
            "choices": [
                {"message": {"content": [{"text": "Image description"}]}},
            ],
        }

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_image_to_text(
                image_urls="https://example.com/image.jpg",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Image description",
            ),
        ]

    def test_image_to_text_none_content(self) -> None:
        """Test handling of None content in response"""
        mock_dashscope = MagicMock()
        mock_dashscope.MultiModalConversation.call.return_value.output = {
            "choices": [{"message": {"content": None}}],
        }

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_image_to_text(
                image_urls="https://example.com/image.jpg",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Error: Failed to generate text",
            ),
        ]

    def test_image_to_text_exception(self) -> None:
        """Test exception handling"""
        mock_dashscope = MagicMock()
        mock_dashscope.MultiModalConversation.call.side_effect = Exception(
            "API Error",
        )

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_image_to_text(
                image_urls="https://example.com/image.jpg",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Failed to generate text: API Error",
            ),
        ]


class TestDashScopeTextToAudio:
    """Test cases for dashscope_text_to_audio function"""

    def test_text_to_audio_success(self) -> None:
        """Test successful audio generation"""
        fake_audio_data = b"fake_audio_data"
        expected_base64 = base64.b64encode(fake_audio_data).decode("utf-8")

        mock_dashscope = MagicMock()
        mock_response = Mock()
        mock_response.get_audio_data.return_value = fake_audio_data
        mock_dashscope.audio.tts.SpeechSynthesizer.call.return_value = (
            mock_response
        )

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_text_to_audio(
                text="Hello world",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            AudioBlock(
                type="audio",
                source={
                    "type": "base64",
                    "media_type": "audio/wav",
                    "data": expected_base64,
                },
            ),
        ]

    def test_text_to_audio_no_data(self) -> None:
        """Test when no audio data is returned"""
        mock_dashscope = MagicMock()
        mock_response = Mock()
        mock_response.get_audio_data.return_value = None
        mock_dashscope.audio.tts.SpeechSynthesizer.call.return_value = (
            mock_response
        )

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_text_to_audio(
                text="Hello world",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Error: Failed to generate audio",
            ),
        ]

    def test_text_to_audio_api_exception(self) -> None:
        """Test exception handling during API call"""
        mock_dashscope = MagicMock()
        mock_dashscope.audio.tts.SpeechSynthesizer.call.side_effect = (
            Exception(
                "TTS API Error",
            )
        )

        with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
            result = dashscope_text_to_audio(
                text="Hello world",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Failed to generate audio: TTS API Error",
            ),
        ]
