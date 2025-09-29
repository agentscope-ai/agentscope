# -*- coding: utf-8 -*-
"""The multi-modal-related tools module in agentscope."""
from ._dashscope_tools import (
    dashscope_image_to_text,
    dashscope_text_to_audio,
    dashscope_text_to_image,
)
from ._openai_tools import (
    openai_text_to_image,
    openai_edit_image,
    openai_text_to_audio,
    openai_create_image_variation,
    openai_image_to_text,
    openai_audio_to_text,
)

__all__ = [
    "dashscope_image_to_text",
    "dashscope_text_to_audio",
    "dashscope_text_to_image",
    "openai_text_to_image",
    "openai_text_to_audio",
    "openai_edit_image",
    "openai_create_image_variation",
    "openai_image_to_text",
    "openai_audio_to_text",
]
