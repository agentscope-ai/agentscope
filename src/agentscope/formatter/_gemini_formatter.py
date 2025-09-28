# -*- coding: utf-8 -*-
"""The gemini formatter class."""
import base64
import os
from typing import Union, Any
from urllib.parse import urlparse

from ._formatter_base import FormatterBase
from ._truncated_formatter_base import TruncatedFormatterBase
from .._utils._common import _get_bytes_from_web_url
from ..message import Msg, TextBlock, ImageBlock, VideoBlock, AudioBlock, ToolUseBlock, ToolResultBlock
from ..token import TokenCounterBase
from .._logging import logger


def _to_gemini_inline_data(url: str) -> dict:
    """Convert url into the Gemini API required format."""
    parsed_url = urlparse(url)
    extension = url.split(".")[-1].lower()

    # Pre-calculate media type from extension (image/audio/video).
    typ = None
    for k, v in GeminiChatFormatter.supported_extensions.items():
        if extension in v:
            typ = k
            break

    if not os.path.exists(url) and parsed_url.scheme != "":
        # Web url
        if typ is None:
            raise TypeError(
                f"Unsupported file extension: {extension}, expected "
                f"{GeminiChatFormatter.supported_extensions}",
            )

        data = _get_bytes_from_web_url(url)
        return {
            "data": data,
            "mime_type": f"{typ}/{extension}",
        }

    elif os.path.exists(url):
        # Local file
        if typ is None:
            raise TypeError(
                f"Unsupported file extension: {extension}, expected "
                f"{GeminiChatFormatter.supported_extensions}",
            )

        with open(url, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")

        return {
            "data": data,
            "mime_type": f"{typ}/{extension}",
        }

    raise ValueError(
        f"The URL `{url}` is not a valid image URL or local file.",
    )


class GeminiChatFormatter(TruncatedFormatterBase):
    """The formatter for Google Gemini API."""

    support_tools_api: bool = True
    """Whether support tools API"""

    support_multiagent: bool = False
    """Whether support multi-agent conversations"""

    support_vision: bool = True
    """Whether support vision data"""

    supported_blocks: list[type] = [
        TextBlock,
        # Multimodal
        ImageBlock,
        VideoBlock,
        AudioBlock,
        # Tool use
        ToolUseBlock,
        ToolResultBlock,
    ]
    """The list of supported message blocks"""

    supported_extensions: dict[str, list[str]] = {
        "image": ["png", "jpeg", "webp", "heic", "heif"],
        "video": [
            "mp4",
            "mpeg",
            "mov",
            "avi",
            "x-flv",
            "mpg",
            "webm",
            "wmv",
            "3gpp",
        ],
        "audio": ["mp3", "wav", "aiff", "aac", "ogg", "flac"],
    }

    async def _format(
        self,
        msgs: list[Msg],
    ) -> list[dict]:
        """Format message objects into Gemini API required format."""
        self.assert_list_of_msgs(msgs)

        messages: list = []
        for msg in msgs:
            parts = []

            for block in msg.get_content_blocks():
                typ = block.get("type")
                if typ == "text":
                    parts.append(
                        {
                            "text": block.get("text"),
                        },
                    )

                elif typ == "tool_use":
                    parts.append(
                        {
                            "function_call": {
                                "id": block["id"],
                                "name": block["name"],
                                "args": block["input"],
                            },
                        },
                    )

                elif typ == "tool_result":
                    text_output = self.convert_tool_result_to_string(
                        block["output"],  # type: ignore[arg-type]
                    )
                    messages.append(
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "function_response": {
                                        "id": block["id"],
                                        "name": block["name"],
                                        "response": {
                                            "output": text_output,
                                        },
                                    },
                                },
                            ],
                        },
                    )

                elif typ in ["image", "audio", "video"]:
                    if block["source"]["type"] == "base64":
                        media_type = block["source"]["media_type"]
                        base64_data = block["source"]["data"]

                        parts.append(
                            {
                                "inline_data": {
                                    "data": base64_data,
                                    "mime_type": media_type,
                                },
                            },
                        )

                    elif block["source"]["type"] == "url":
                        parts.append(
                            {
                                "inline_data": _to_gemini_inline_data(
                                    block["source"]["url"],
                                ),
                            },
                        )

                else:
                    logger.warning(
                        "Unsupported block type: %s in the message, skipped. ",
                        typ,
                    )

            role = "model" if msg.role == "assistant" else "user"

            if parts:
                messages.append(
                    {
                        "role": role,
                        "parts": parts,
                    },
                )

        return messages


class GeminiMultiAgentFormatter(TruncatedFormatterBase):
    """The multi-agent formatter for Google Gemini API, where more than a
    user and an agent are involved.

    .. note:: This formatter will combine previous messages (except tool
     calls/results) into a history section in the first system message with
     the conversation history prompt.

    .. note:: For tool calls/results, they will be presented as separate
     messages as required by the Gemini API. Therefore, the tool calls/
     results messages are expected to be placed at the end of the input
     messages.

    .. tip:: Telling the assistant's name in the system prompt is very
     important in multi-agent conversations. So that LLM can know who it
     is playing as.

    """

    support_tools_api: bool = True
    """Whether support tools API"""

    support_multiagent: bool = True
    """Whether support multi-agent conversations"""

    support_vision: bool = True
    """Whether support vision data"""

    supported_blocks: list[type] = [
        TextBlock,
        # Multimodal
        ImageBlock,
        VideoBlock,
        AudioBlock,
        # Tool use
        ToolUseBlock,
        ToolResultBlock,
    ]
    """The list of supported message blocks"""

    def __init__(
        self,
        conversation_history_prompt: str = (
            "# Conversation History\n"
            "The content between <history></history> tags contains "
            "your conversation history\n"
        ),
        token_counter: TokenCounterBase | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize the Gemini multi-agent formatter.

        Args:
            conversation_history_prompt (`str`):
                The prompt to be used for the conversation history section.
            token_counter (`TokenCounterBase | None`, optional):
                The token counter used for truncation.
            max_tokens (`int | None`, optional):
                The maximum number of tokens allowed in the formatted
                messages. If `None`, no truncation will be applied.
        """
        super().__init__(token_counter=token_counter, max_tokens=max_tokens)
        self.conversation_history_prompt = conversation_history_prompt

    async def _format_system_message(
        self,
        msg: Msg,
    ) -> dict[str, Any]:
        """Format system message for the Gemini API."""
        return {
            "role": "user",
            "parts": [
                {
                    "text": msg.get_text_content(),
                },
            ],
        }

    async def _format_tool_sequence(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Given a sequence of tool call/result messages, format them into
        the required format for the Gemini API.

        Args:
            msgs (`list[Msg]`):
                The list of messages containing tool calls/results to format.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries formatted for the Gemini API.
        """
        return await GeminiChatFormatter().format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into the required format for the Gemini API.

        Args:
            msgs (`list[Msg]`):
                A list of Msg objects to be formatted.
            is_first (`bool`, defaults to `True`):
                Whether this is the first agent message in the conversation.
                If `True`, the conversation history prompt will be included.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries formatted for the Gemini API.
        """

        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        # Format into Gemini API required format
        formatted_msgs: list = []

        # Collect the multimodal files
        conversation_parts: list = []
        accumulated_text = []
        for msg in msgs:
            for block in msg.get_content_blocks():
                if block["type"] == "text":
                    accumulated_text.append(f"{msg.name}: {block['text']}")

                elif block["type"] in ["image", "video", "audio"]:
                    # handle the accumulated text as a single part if exists
                    if accumulated_text:
                        conversation_parts.append(
                            {
                                "text": "\n".join(accumulated_text),
                            },
                        )
                        accumulated_text.clear()

                    # handle the multimodal data
                    if block["source"]["type"] == "url":
                        conversation_parts.append(
                            {
                                "inline_data": _to_gemini_inline_data(
                                    block["source"]["url"],
                                ),
                            },
                        )

                    elif block["source"]["type"] == "base64":
                        media_type = block["source"]["media_type"]
                        base64_data = block["source"]["data"]
                        conversation_parts.append(
                            {
                                "inline_data": {
                                    "data": base64_data,
                                    "mime_type": media_type,
                                },
                            },
                        )

        if accumulated_text:
            conversation_parts.append(
                {
                    "text": "\n".join(accumulated_text),
                },
            )

        # Add prompt and <history></history> tags around conversation history
        if conversation_parts:
            if conversation_parts[0].get("text"):
                conversation_parts[0]["text"] = (
                    conversation_history_prompt
                    + "<history>"
                    + conversation_parts[0]["text"]
                )

            else:
                conversation_parts.insert(
                    0,
                    {"text": conversation_history_prompt + "<history>"},
                )

            if conversation_parts[-1].get("text"):
                conversation_parts[-1]["text"] += "\n</history>"

            else:
                conversation_parts.append(
                    {"text": "</history>"},
                )

            formatted_msgs.append(
                {
                    "role": "user",
                    "parts": conversation_parts,
                },
            )

        return formatted_msgs

class GeminiFormatter(FormatterBase):
    """The formatter for Gemini model."""

    supported_model_regexes: list[str] = [
        "gemini-.*",
    ]

    @classmethod
    def format_chat(
        cls,
        *msgs: Union[Msg, list[Msg], None],
    ) -> list[dict]:
        """Format the messages in chat scenario, where only a user and an
        assistant is involved (maybe with system in the beginning)."""

        msgs = cls.check_and_flat_messages(*msgs)

        formatted_msgs = []
        for msg in msgs:
            if msg is None:
                continue
            if msg.role in ["user", "system"]:
                formatted_msgs.append(
                    {
                        "role": "user",
                        "parts": msg.get_text_content(),
                    },
                )
            elif msg.role == "assistant":
                formatted_msgs.append(
                    {
                        "role": "model",
                        "parts": msg.get_text_content(),
                    },
                )
        return formatted_msgs

    @classmethod
    def format_multi_agent(
        cls,
        *msgs: Union[Msg, list[Msg], None],
    ) -> list[dict]:
        """Format the messages in multi-agent scenario, where multiple agents
        are involved.

        Requirements of Gemini generate API:
        1. In Gemini `generate_content` API, the `role` field must be either
        `"user"` or `"model"` (In our test, `"assistant"` also works).
        2. If the role of the last message is "model", the gemini model will
        treat it as a continuation task.

        The above information is updated to 2025/03/14. More information
        about the Gemini `generate_content` API can be found in
        https://googleapis.github.io/python-genai/#

        Based on the above considerations, we decide to combine all messages
        into a single user message. This is a simple and straightforward
        strategy, if you have any better ideas, pull request and
        discussion are welcome in our GitHub repository
        https://github.com/agentscope/agentscope!
        """
        if len(msgs) == 0:
            raise ValueError(
                "At least one message should be provided. An empty message "
                "list is not allowed.",
            )

        input_msgs = cls.check_and_flat_messages(*msgs)

        # record dialog history as a list of strings
        sys_prompt = None
        dialogue = []
        for i, unit in enumerate(input_msgs):
            if i == 0 and unit.role == "system":
                # system prompt
                sys_prompt = unit.get_text_content()
            else:
                # Merge all messages into a conversation history prompt
                text_content = unit.get_text_content()
                if text_content is not None:
                    dialogue.append(
                        f"{unit.name}: {text_content}",
                    )

        prompt_components = []
        if sys_prompt is not None:
            if not sys_prompt.endswith("\n"):
                sys_prompt += "\n"
            prompt_components.append(sys_prompt)

        if len(dialogue) > 0:
            prompt_components.extend(["## Conversation History"] + dialogue)

        user_prompt = "\n".join(prompt_components)

        messages = [
            {
                "role": "user",
                "parts": [
                    user_prompt,
                ],
            },
        ]

        return messages
