# -*- coding: utf-8 -*-
"""The formatter for Anthropic model."""
from typing import Union, Any

from loguru import logger

from ._formatter_base import FormatterBase
from ._truncated_formatter_base import TruncatedFormatterBase
from ..message import Msg, TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock
from ..token import TokenCounterBase
from ..utils.common import _to_anthropic_image_url


class AnthropicFormatter(FormatterBase):
    """The formatter for Anthropic model."""

    supported_model_regexes: list[str] = [
        "claude-3-5.*",
        "claude-3-7.*",
    ]

    @classmethod
    def format_chat(
        cls,
        *msgs: Union[Msg, list[Msg], None],
    ) -> list[dict]:
        """Format the messages in chat scenario, where only one user and
        one assistant are involved.

        Args:
            msgs (`Union[Msg, list[Msg], None]`):
                The message(s) to be formatted. The `None` input will be
                ignored.
        """

        msgs = cls.check_and_flat_messages(*msgs)

        formatted_msgs = []
        for index, msg in enumerate(msgs):
            content = []
            for block in msg.get_content_blocks():
                if block.get("type") == "text":
                    content.append(
                        {
                            "type": "text",
                            "text": block.get("text"),
                        },
                    )
                elif block.get("type") == "image":
                    content.append(
                        {
                            "type": "image",
                            "source": _to_anthropic_image_url(
                                str(block.get("url")),
                            ),
                        },
                    )
                elif block.get("type") == "tool_use":
                    content.append(dict(block))
                elif block.get("type") == "tool_result":
                    content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.get("id"),
                            "content": block.get("output"),
                        },
                    )
                else:
                    logger.warning(
                        f"Unsupported block type: {block.get('type')}",
                        "skipped",
                    )

            # Claude only allow the first message to be system message
            if msg.role == "system" and index != 0:
                role = "user"
            else:
                role = msg.role

            formatted_msgs.append(
                {
                    "role": role,
                    "content": content,
                },
            )
        return formatted_msgs

    @classmethod
    def format_multi_agent(
        cls,
        *msgs: Union[Msg, list[Msg], None],
    ) -> list[dict]:
        """Format the messages in multi-agent scenario, where multiple agents
        are involved."""

        # Parse all information into a list of messages
        input_msgs = cls.check_and_flat_messages(*msgs)

        if len(input_msgs) == 0:
            raise ValueError(
                "At least one message should be provided. An empty message "
                "list is not allowed.",
            )

        # record dialog history as a list of strings
        dialogue = []
        image_blocks = []
        sys_prompt = None
        for i, msg in enumerate(input_msgs):
            if i == 0 and msg.role == "system":
                # if system prompt is available, place it at the beginning
                sys_prompt = msg.get_text_content()
            else:
                # Merge all messages into a conversation history prompt
                for block in msg.get_content_blocks():
                    typ = block.get("type")
                    if typ == "text":
                        dialogue.append(
                            f"{msg.name}: {block.get('text')}",
                        )
                    elif typ == "tool_use":
                        dialogue.append(
                            f"<tool_use>{block}</tool_use>",
                        )
                    elif typ == "tool_result":
                        dialogue.append(
                            f"<tool_result>{block}</tool_result>",
                        )
                    elif typ == "image":
                        image_blocks.append(
                            {
                                "type": "image",
                                "source": _to_anthropic_image_url(
                                    str(block.get("url")),
                                ),
                            },
                        )

        content_components = []

        # The conversation history is added to the user message if not empty
        if len(dialogue) > 0:
            content_components.extend(["## Conversation History"] + dialogue)

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "\n".join(content_components),
                    },
                ]
                + image_blocks,
            },
        ]

        # Add system prompt at the beginning if provided
        if sys_prompt is not None:
            messages = [{"role": "system", "content": sys_prompt}] + messages

        return messages

    @classmethod
    def format_tools_json_schemas(cls, schemas: dict[str, dict]) -> list[dict]:
        """Format the JSON schemas of the tool functions to the format that
        Anthropic API expects. This function will take the parsed JSON schema
        from `agentscope.service.ServiceToolkit` as input and return the
        formatted JSON schema.

        Note:
            An example of the input tool JSON schema

            ..code-block:: json

                {
                    "bing_search": {
                        "type": "function",
                        "function": {
                            "name": "bing_search",
                            "description": "Search the web using Bing.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "The search query.",
                                    }
                                },
                                "required": ["query"],
                            }
                        }
                    }
                }

            The formatted JSON schema will be like:

            ..code-block:: json

                [
                    {
                        "name": "bing_search",
                        "description": "Search the web using Bing.",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "The search query.",
                                }
                            },
                            "required": ["query"],
                        }
                    }
                ]

        Args:
            schemas (`dict[str, dict]`):
                The JSON schema of the tool functions, where the key is the
                function name, and the value is the schema.

        Returns:
            `list[dict]`:
                The formatted JSON schema.
        """

        assert isinstance(
            schemas,
            dict,
        ), f"Expect a dict of schemas, got {type(schemas)}."

        formatted_schemas = []
        for schema in schemas.values():
            assert (
                "function" in schema
            ), f"Invalid schema: {schema}, expect key 'function'."

            assert "name" in schema["function"], (
                f"Invalid schema: {schema}, "
                "expect key 'name' in 'function' field."
            )

            formatted_schemas.append(
                {
                    "name": schema["function"]["name"],
                    "description": schema["function"].get("description", ""),
                    "input_schema": schema["function"].get("parameters", {}),
                },
            )

        return formatted_schemas


class AnthropicChatFormatter(TruncatedFormatterBase):
    """Formatter for Anthropic messages."""

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
        # Tool use
        ToolUseBlock,
        ToolResultBlock,
    ]
    """The list of supported message blocks"""

    async def _format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into Anthropic API format.

        Args:
            msgs (`list[Msg]`):
                The list of message objects to format.

        Returns:
            `list[dict[str, Any]]`:
                The formatted messages as a list of dictionaries.

        .. note:: Anthropic suggests always passing all previous thinking
         blocks back to the API in subsequent calls to maintain reasoning
         continuity. For more details, please refer to
         `Anthropic's documentation
         <https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking#preserving-thinking-blocks>`_.
        """
        self.assert_list_of_msgs(msgs)

        messages: list[dict] = []
        for index, msg in enumerate(msgs):
            content_blocks = []

            for block in msg.get_content_blocks():
                typ = block.get("type")
                if typ in ["thinking", "text", "image"]:
                    content_blocks.append({**block})

                elif typ == "tool_use":
                    content_blocks.append(
                        {
                            "id": block.get("id"),
                            "type": "tool_use",
                            "name": block.get("name"),
                            "input": block.get("input", {}),
                        },
                    )

                elif typ == "tool_result":
                    output = block.get("output")
                    if output is None:
                        content_value = [{"type": "text", "text": None}]
                    elif isinstance(output, list):
                        content_value = output
                    else:
                        content_value = [{"type": "text", "text": str(output)}]
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.get("id"),
                                    "content": content_value,
                                },
                            ],
                        },
                    )
                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        typ,
                    )

            # Claude only allow the first message to be system message
            if msg.role == "system" and index != 0:
                role = "user"
            else:
                role = msg.role

            msg_anthropic = {
                "role": role,
                "content": content_blocks or None,
            }

            # When both content and tool_calls are None, skipped
            if msg_anthropic["content"] or msg_anthropic.get("tool_calls"):
                messages.append(msg_anthropic)

        return messages


class AnthropicMultiAgentFormatter(TruncatedFormatterBase):
    """
    Anthropic formatter for multi-agent conversations, where more than
    a user and an agent are involved.
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
        """Initialize the DashScope multi-agent formatter.

        Args:
            conversation_history_prompt (`str`):
                The prompt to use for the conversation history section.
        """
        super().__init__(token_counter=token_counter, max_tokens=max_tokens)
        self.conversation_history_prompt = conversation_history_prompt

    async def _format_tool_sequence(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Given a sequence of tool call/result messages, format them into
        the required format for the Anthropic API."""
        return await AnthropicChatFormatter().format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into the required format for the Anthropic API."""

        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        # Format into required Anthropic format
        formatted_msgs: list[dict] = []

        # Collect the multimodal files
        conversation_blocks: list = []
        accumulated_text = []
        for msg in msgs:
            for block in msg.get_content_blocks():
                if block["type"] == "text":
                    accumulated_text.append(f"{msg.name}: {block['text']}")

                elif block["type"] == "image":
                    # Handle the accumulated text as a single block
                    if accumulated_text:
                        conversation_blocks.append(
                            {
                                "text": "\n".join(accumulated_text),
                                "type": "text",
                            },
                        )
                        accumulated_text.clear()

                    conversation_blocks.append({**block})

        if accumulated_text:
            conversation_blocks.append(
                {
                    "text": "\n".join(accumulated_text),
                    "type": "text",
                },
            )

        if conversation_blocks:
            if conversation_blocks[0].get("text"):
                conversation_blocks[0]["text"] = (
                    conversation_history_prompt
                    + "<history>\n"
                    + conversation_blocks[0]["text"]
                )

            else:
                conversation_blocks.insert(
                    0,
                    {
                        "type": "text",
                        "text": conversation_history_prompt + "<history>\n",
                    },
                )

            if conversation_blocks[-1].get("text"):
                conversation_blocks[-1]["text"] += "\n</history>"

            else:
                conversation_blocks.append(
                    {"type": "text", "text": "</history>"},
                )

        if conversation_blocks:
            formatted_msgs.append(
                {
                    "role": "user",
                    "content": conversation_blocks,
                },
            )

        return formatted_msgs
