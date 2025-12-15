# -*- coding: utf-8 -*-
"""The tool module in agentscope."""

from ._response import ToolResponse
from ._coding import (
    execute_python_code,
    execute_shell_command,
)
from ._text_file import (
    view_text_file,
    write_text_file,
    insert_text_file,
)
from ._multi_modality import (
    dashscope_text_to_image,
    dashscope_text_to_audio,
    dashscope_image_to_text,
    openai_text_to_image,
    openai_text_to_audio,
    openai_edit_image,
    openai_create_image_variation,
    openai_image_to_text,
    openai_audio_to_text,
)
from ._toolkit import Toolkit
from ._web import web_search
from ._database.sqlite import execute_sql, get_schema
from ._speech.speech import text_to_speech, speech_to_text

__all__ = [
    "Toolkit",
    "ToolResponse",
    "execute_python_code",
    "execute_shell_command",
    "view_text_file",
    "write_text_file",
    "insert_text_file",
    "dashscope_text_to_image",
    "dashscope_text_to_audio",
    "dashscope_image_to_text",
    "openai_text_to_image",
    "openai_text_to_audio",
    "openai_edit_image",
    "openai_create_image_variation",
    "openai_image_to_text",
    "openai_image_to_text",
    "openai_audio_to_text",
    "web_search",
    "execute_sql",
    "get_schema",
    "text_to_speech",
    "speech_to_text",
]
