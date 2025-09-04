# -*- coding: utf-8 -*-
"""Unit tests for OpenAI tools"""

import base64
from io import BytesIO
from unittest.mock import Mock, patch, mock_open, MagicMock

from agentscope.message import ImageBlock, TextBlock, AudioBlock
from agentscope.tool import ToolResponse
from agentscope.tool import (
    openai_text_to_image,
    openai_edit_image,
    openai_create_image_variation,
    openai_image_to_text,
    openai_text_to_audio,
    openai_audio_to_text,
)


class TestOpenAITextToImage:
    """Test cases for openai_text_to_image function"""

    def test_text_to_image_success_url_mode(self) -> None:
        """Test successful image generation in URL mode"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_response = Mock()
        mock_response.data = [
            Mock(url="https://example.com/image1.jpg"),
            Mock(url="https://example.com/image2.png"),
        ]
        mock_client.images.generate.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = openai_text_to_image(
                prompt="A beautiful landscape",
                api_key="test_key",
                n=2,
                response_format="url",
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
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        fake_base64_data = base64.b64encode(b"fake_image_data").decode("utf-8")
        mock_response = Mock()
        mock_response.data = [Mock(b64_json=fake_base64_data)]
        mock_client.images.generate.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = openai_text_to_image(
                prompt="A beautiful landscape",
                api_key="test_key",
                n=1,
                response_format="b64_json",
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            ImageBlock(
                type="image",
                source={
                    "type": "base64",
                    "media_type": "image/png",
                    "data": fake_base64_data,
                },
            ),
        ]

    def test_text_to_image_gpt_image_1_force_base64(self) -> None:
        """Test gpt-image-1 forces base64 format"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        fake_base64_data = base64.b64encode(b"fake_image_data").decode("utf-8")
        mock_response = Mock()
        mock_response.data = [Mock(b64_json=fake_base64_data)]
        mock_client.images.generate.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = openai_text_to_image(
                prompt="A beautiful landscape",
                api_key="test_key",
                model="gpt-image-1",
                response_format="url",  # Should be ignored
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            ImageBlock(
                type="image",
                source={
                    "type": "base64",
                    "media_type": "image/png",
                    "data": fake_base64_data,
                },
            ),
        ]

    def test_text_to_image_exception(self) -> None:
        """Test exception handling"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.images.generate.side_effect = Exception("API Error")

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = openai_text_to_image(
                prompt="A beautiful landscape",
                api_key="test_key",
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Failed to generate image: API Error",
            ),
        ]


class TestOpenAIEditImage:
    """Test cases for openai_edit_image function"""

    def test_edit_image_success_url_input(self) -> None:
        """Test successful image editing with URL input"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_api_response = Mock()
        mock_api_response.data = [Mock(url="https://example.com/edited.jpg")]
        mock_client.images.edit.return_value = mock_api_response

        with patch.dict("sys.modules", {"openai": mock_openai}), patch(
            "agentscope.tool._multi_modality._openai_tools.requests.get",
        ) as mock_requests, patch("PIL.Image.open") as mock_image_open:
            mock_response = Mock()
            mock_response.content = b"fake_image_data"
            mock_response.raise_for_status.return_value = None
            mock_requests.return_value = mock_response

            mock_img = Mock()
            mock_img.mode = "RGB"
            mock_img.convert.return_value = mock_img
            mock_image_open.return_value = mock_img

            result = openai_edit_image(
                image_url="https://example.com/image.jpg",
                prompt="Add a sunset",
                api_key="test_key",
                response_format="url",
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            ImageBlock(
                type="image",
                source={
                    "type": "url",
                    "url": "https://example.com/edited.jpg",
                },
            ),
        ]

    def test_edit_image_success_local_file(self) -> None:
        """Test successful image editing with local file"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        fake_base64_data = base64.b64encode(b"fake_edited_data").decode(
            "utf-8",
        )
        mock_api_response = Mock()
        mock_api_response.data = [Mock(b64_json=fake_base64_data)]
        mock_client.images.edit.return_value = mock_api_response

        with (
            patch.dict("sys.modules", {"openai": mock_openai}),
            patch("PIL.Image.open") as mock_image_open,
        ):
            mock_img = Mock()
            mock_img.mode = "RGBA"  # Already RGBA
            mock_image_open.return_value = mock_img

            result = openai_edit_image(
                image_url="./local_image.jpg",
                prompt="Add a sunset",
                api_key="test_key",
                model="gpt-image-1",  # Forces base64
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            ImageBlock(
                type="image",
                source={
                    "type": "base64",
                    "media_type": "image/png",
                    "data": fake_base64_data,
                },
            ),
        ]

    def test_edit_image_exception(self) -> None:
        """Test exception handling"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.images.edit.side_effect = Exception("Edit Error")

        with (
            patch.dict("sys.modules", {"openai": mock_openai}),
            patch("PIL.Image.open") as mock_image_open,
        ):
            mock_img = Mock()
            mock_img.mode = "RGB"
            mock_img.convert.return_value = mock_img
            mock_image_open.return_value = mock_img

            result = openai_edit_image(
                image_url="./image.jpg",
                prompt="Edit image",
                api_key="test_key",
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Failed to generate image: Edit Error",
            ),
        ]

    def test_edit_image_file_not_found(self) -> None:
        """Test file not found error handling"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = openai_edit_image(
                image_url="./nonexistent_image.jpg",
                prompt="Edit image",
                api_key="test_key",
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Failed to generate image: [Errno 2] No such file or "
                "directory: './nonexistent_image.jpg'",
            ),
        ]


class TestOpenAICreateImageVariation:
    """Test cases for openai_create_image_variation function"""

    def test_create_variation_success_url_mode(self) -> None:
        """Test successful image variation creation in URL mode"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_response = Mock()
        mock_response.data = [
            Mock(url="https://example.com/variation1.jpg"),
            Mock(url="https://example.com/variation2.jpg"),
        ]
        mock_client.images.create_variation.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai}), patch(
            "agentscope.tool._multi_modality._openai_tools._parse_url",
        ) as mock_parse_url:
            mock_parse_url.return_value = BytesIO(b"fake_image_data")

            result = openai_create_image_variation(
                image_url="https://example.com/image.jpg",
                api_key="test_key",
                n=2,
                response_format="url",
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            ImageBlock(
                type="image",
                source={
                    "type": "url",
                    "url": "https://example.com/variation1.jpg",
                },
            ),
            ImageBlock(
                type="image",
                source={
                    "type": "url",
                    "url": "https://example.com/variation2.jpg",
                },
            ),
        ]

    def test_create_variation_success_base64_mode(self) -> None:
        """Test successful image variation creation in base64 mode"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        fake_base64_data = base64.b64encode(b"fake_variation").decode("utf-8")
        mock_response = Mock()
        mock_response.data = [Mock(b64_json=fake_base64_data)]
        mock_client.images.create_variation.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai}), patch(
            "agentscope.tool._multi_modality._openai_tools._parse_url",
        ) as mock_parse_url:
            mock_parse_url.return_value = BytesIO(b"fake_image_data")

            result = openai_create_image_variation(
                image_url="./image.jpg",
                api_key="test_key",
                response_format="b64_json",
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            ImageBlock(
                type="image",
                source={
                    "type": "base64",
                    "media_type": "image/png",
                    "data": fake_base64_data,
                },
            ),
        ]

    def test_create_variation_exception(self) -> None:
        """Test exception handling"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.images.create_variation.side_effect = Exception(
            "Variation Error",
        )

        with patch.dict("sys.modules", {"openai": mock_openai}), patch(
            "agentscope.tool._multi_modality._openai_tools._parse_url",
        ) as mock_parse_url:
            mock_parse_url.return_value = BytesIO(b"fake_image_data")

            result = openai_create_image_variation(
                image_url="./image.jpg",
                api_key="test_key",
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Failed to generate image: Variation Error",
            ),
        ]


class TestOpenAIImageToText:
    """Test cases for openai_image_to_text function"""

    def test_image_to_text_single_url_success(self) -> None:
        """Test successful processing of single image URL"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_message = Mock()
        mock_message.content = "This is a beautiful landscape"
        mock_response = Mock()
        mock_response.choices = [Mock(message=mock_message)]
        mock_client.chat.completions.create.return_value = mock_response

        with (
            patch.dict("sys.modules", {"openai": mock_openai}),
            patch(
                "agentscope.tool._multi_modality._openai_tools."
                "_to_openai_image_url",
            ) as mock_to_url,
        ):
            mock_to_url.return_value = "https://example.com/image.jpg"

            result = openai_image_to_text(
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
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_message = Mock()
        mock_message.content = "Multiple images description"
        mock_response = Mock()
        mock_response.choices = [Mock(message=mock_message)]
        mock_client.chat.completions.create.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai}), patch(
            "agentscope.tool._multi_modality._openai_tools."
            "_to_openai_image_url",
        ) as mock_to_url:
            mock_to_url.side_effect = [
                "https://example.com/1.jpg",
                "https://example.com/2.jpg",
            ]

            result = openai_image_to_text(
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

    def test_image_to_text_exception(self) -> None:
        """Test exception handling"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception(
            "Vision API Error",
        )

        with (
            patch.dict(
                "sys.modules",
                {"openai": mock_openai},
            ),
            patch(
                "agentscope.tool._multi_modality._openai_tools."
                "_to_openai_image_url",
            ) as mock_to_url,
        ):
            mock_to_url.return_value = "https://example.com/image.jpg"

            result = openai_image_to_text(
                image_urls="https://example.com/image.jpg",
                api_key="test_key",
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Failed to generate text: Vision API Error",
            ),
        ]


class TestOpenAITextToAudio:
    """Test cases for openai_text_to_audio function"""

    def test_text_to_audio_success(self) -> None:
        """Test successful audio generation"""
        fake_audio_data = b"fake_audio_data"
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_response = Mock()
        mock_response.content = fake_audio_data
        mock_client.audio.speech.create.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = openai_text_to_audio(
                text="Hello world",
                api_key="test_key",
            )
        expected_base64 = base64.b64encode(fake_audio_data).decode("utf-8")
        assert isinstance(result, ToolResponse)
        assert result.content == [
            AudioBlock(
                type="audio",
                source={
                    "type": "base64",
                    "media_type": "audio/mp3",
                    "data": expected_base64,
                },
            ),
        ]

    def test_text_to_audio_different_format(self) -> None:
        """Test audio generation with different format"""
        fake_audio_data = b"fake_wav_data"
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_response = Mock()
        mock_response.content = fake_audio_data
        mock_client.audio.speech.create.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = openai_text_to_audio(
                text="Hello world",
                api_key="test_key",
                res_format="wav",
                voice="echo",
                speed=1.2,
            )
        expected_base64 = base64.b64encode(fake_audio_data).decode("utf-8")
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

    def test_text_to_audio_exception(self) -> None:
        """Test exception handling"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.audio.speech.create.side_effect = Exception(
            "TTS API Error",
        )

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = openai_text_to_audio(
                text="Hello world",
                api_key="test_key",
            )
        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Error: Failed to generate audio. TTS API Error",
            ),
        ]


class TestOpenAIAudioToText:
    """Test cases for openai_audio_to_text function"""

    def test_audio_to_text_local_file_success(self) -> None:
        """Test successful processing of local audio file"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_transcription = Mock()
        mock_transcription.text = "Hello, this is a test transcription"
        mock_client.audio.transcriptions.create.return_value = (
            mock_transcription
        )

        with (
            patch.dict("sys.modules", {"openai": mock_openai}),
            patch(
                "agentscope.tool._multi_modality._openai_tools.os.path.exists",
            ) as mock_exists,
            patch(
                "builtins.open",
                mock_open(read_data=b"fake_audio_data"),
            ),
        ):
            mock_exists.return_value = True

            result = openai_audio_to_text(
                audio_file_url="./audio.mp3",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Hello, this is a test transcription",
            ),
        ]

    def test_audio_to_text_url_success(self) -> None:
        """Test successful processing of audio URL"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_transcription = Mock()
        mock_transcription.text = "URL audio transcription"
        mock_client.audio.transcriptions.create.return_value = (
            mock_transcription
        )

        with patch.dict("sys.modules", {"openai": mock_openai}), patch(
            "agentscope.tool._multi_modality._openai_tools.requests.get",
        ) as mock_requests:
            # Mock requests
            mock_response = Mock()
            mock_response.content = b"fake_audio_data"
            mock_response.raise_for_status.return_value = None
            mock_requests.return_value = mock_response

            result = openai_audio_to_text(
                audio_file_url="https://example.com/audio.mp3",
                api_key="test_key",
                language="zh",
                temperature=0.5,
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="URL audio transcription",
            ),
        ]

    def test_audio_to_text_file_not_found(self) -> None:
        """Test error handling when local file not found"""
        with patch(
            "agentscope.tool._multi_modality._openai_tools.os.path.exists",
        ) as mock_exists:
            mock_exists.return_value = False

            result = openai_audio_to_text(
                audio_file_url="./nonexistent.mp3",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Error: Failed to transcribe audio: File not found: "
                "./nonexistent.mp3",
            ),
        ]

    def test_audio_to_text_api_exception(self) -> None:
        """Test exception handling during API call"""
        mock_openai = MagicMock()
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.audio.transcriptions.create.side_effect = Exception(
            "Transcription Error",
        )

        with (
            patch.dict("sys.modules", {"openai": mock_openai}),
            patch(
                "agentscope.tool._multi_modality._openai_tools.requests.get",
            ) as mock_requests,
        ):
            # Mock requests
            mock_response = Mock()
            mock_response.content = b"fake_audio_data"
            mock_response.raise_for_status.return_value = None
            mock_requests.return_value = mock_response

            result = openai_audio_to_text(
                audio_file_url="https://example.com/audio.mp3",
                api_key="test_key",
            )

        assert isinstance(result, ToolResponse)
        assert result.content == [
            TextBlock(
                type="text",
                text="Error: Failed to transcribe audio: Transcription Error",
            ),
        ]
