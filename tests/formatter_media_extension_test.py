# -*- coding: utf-8 -*-
"""The temp file saved for an unsupported media type keeps a stable name."""
import re
import tempfile
from functools import partial
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from agentscope.formatter import OpenAIResponseFormatter
from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)

_NO_HOST_TABLES = (
    "agentscope.formatter._formatter_base.mimetypes.guess_extension"
)


class MediaTypeExtensionTest(TestCase):
    """Resolve extensions without depending on the host MIME tables."""

    def test_framework_audio_types_are_pinned(self) -> None:
        """Every type the adapters emit survives a host without a mapping."""
        from agentscope.formatter._formatter_base import (  # noqa: PLC0415
            _MEDIA_TYPE_EXTENSIONS,
            _media_type_extension,
        )

        with patch(_NO_HOST_TABLES, return_value=None):
            resolved = {
                media_type: _media_type_extension(media_type)
                for media_type in _MEDIA_TYPE_EXTENSIONS
            }
        self.assertEqual(resolved, _MEDIA_TYPE_EXTENSIONS)

    def test_wav_and_pcm_never_lose_their_extension(self) -> None:
        """``audio/wav`` and ``audio/pcm`` are unmapped on Linux hosts."""
        from agentscope.formatter._formatter_base import (  # noqa: PLC0415
            _media_type_extension,
        )

        with patch(_NO_HOST_TABLES, return_value=None):
            self.assertEqual(_media_type_extension("audio/wav"), ".wav")
            self.assertEqual(_media_type_extension("audio/pcm"), ".pcm")

    def test_parameters_are_ignored(self) -> None:
        """A parameterised media type resolves like its bare form."""
        from agentscope.formatter._formatter_base import (  # noqa: PLC0415
            _media_type_extension,
        )

        with patch(_NO_HOST_TABLES, return_value=None):
            self.assertEqual(
                _media_type_extension("audio/l16;rate=24000;channels=1"),
                ".l16",
            )

    def test_case_and_padding_are_ignored(self) -> None:
        """Media types are matched case-insensitively and trimmed."""
        from agentscope.formatter._formatter_base import (  # noqa: PLC0415
            _media_type_extension,
        )

        with patch(_NO_HOST_TABLES, return_value=None):
            self.assertEqual(_media_type_extension(" AUDIO/WAV "), ".wav")

    def test_host_tables_are_consulted_for_other_types(self) -> None:
        """Types outside the table still use the host's own mapping."""
        from agentscope.formatter._formatter_base import (  # noqa: PLC0415
            _media_type_extension,
        )

        with patch(_NO_HOST_TABLES, return_value=".zzz"):
            self.assertEqual(_media_type_extension("image/tiff"), ".zzz")

    def test_unmapped_type_falls_back(self) -> None:
        """An unknown type gets a generic extension, never none."""
        from agentscope.formatter._formatter_base import (  # noqa: PLC0415
            _DEFAULT_MEDIA_EXTENSION,
            _media_type_extension,
        )

        with patch(_NO_HOST_TABLES, return_value=None):
            self.assertEqual(
                _media_type_extension("application/x-nonsense"),
                _DEFAULT_MEDIA_EXTENSION,
            )


class UnsupportedAudioFallbackTest(IsolatedAsyncioTestCase):
    """An audio result the provider cannot take is saved as a real file."""

    async def test_saved_audio_path_keeps_its_extension(self) -> None:
        """The reported path ends in ``.wav`` even with no host mapping."""
        fmt = OpenAIResponseFormatter()
        named_temp_file = tempfile.NamedTemporaryFile

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "agentscope.formatter._formatter_base.tempfile."
                    "NamedTemporaryFile",
                    side_effect=partial(named_temp_file, dir=temp_dir),
                ),
                patch(_NO_HOST_TABLES, return_value=None),
            ):
                res = await fmt.format(
                    [
                        AssistantMsg(
                            name="assistant",
                            content=[
                                ToolCallBlock(
                                    id="call_audio",
                                    name="get_audio",
                                    input="{}",
                                ),
                                ToolResultBlock(
                                    id="call_audio",
                                    name="get_audio",
                                    output=[
                                        DataBlock(
                                            source=Base64Source(
                                                data="UklGRiQAAABXQVZF",
                                                media_type="audio/wav",
                                            ),
                                        ),
                                    ],
                                    state=ToolResultState.SUCCESS,
                                ),
                            ],
                        ),
                    ],
                )

            output = res[1]["output"]
            self.assertIsInstance(output, str)
            path_match = re.search(
                r"saved locally at: (?P<path>.+)\.</system-reminder>",
                output,
            )
            self.assertIsNotNone(path_match)
            audio_path = Path(path_match.group("path"))
            self.assertEqual(audio_path.suffix, ".wav")
            with audio_path.open("rb") as audio_file:
                self.assertEqual(
                    audio_file.read(),
                    b"RIFF$\x00\x00\x00WAVE",
                )
            self.assertIn("A(n) audio file is returned", output)

        self.assertFalse(audio_path.exists())
