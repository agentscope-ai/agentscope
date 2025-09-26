# -*- coding: utf-8 -*-
"""The formatter for OpenAI model."""
import json
from typing import Union

from loguru import logger

from ._formatter_base import FormatterBase
from ..message import Msg
from ..utils.common import _to_openai_image_url


class OpenAIFormatter(FormatterBase):
    """The formatter for OpenAI model, which is responsible for formatting
    messages, JSON schemas description of the tool functions."""

    supported_model_regexes: list[str] = [
        "gpt-.*",
        "o1",
        "o1-mini",
        "o3-mini",
    ]

    @classmethod
    def format_chat(
        cls,
        *msgs: Union[Msg, list[Msg], None],
    ) -> list[dict]:
        """Format the messages in chat scenario, where only one user and one
        assistant are involved.

        For OpenAI model, the `name` field can be used to distinguish
        different agents (even with the same role as `"assistant"`). So we
        simply reuse the `format_multi_agent` here.
        """
        return cls.format_multi_agent(*msgs)

    @classmethod
    def format_multi_agent(
        cls,
        *msgs: Union[Msg, list[Msg], None],
    ) -> list[dict]:
        """Format the messages in multi-agent scenario, where multiple agents
        are involved.

        For OpenAI model, the `name` field can be used to distinguish
        different agents (even with the same role as `"assistant"`).
        """

        msgs = cls.check_and_flat_messages(*msgs)

        messages = []
        for msg in msgs:
            content_blocks = []
            tool_calls = []
            for block in msg.get_content_blocks():
                typ = block.get("type")
                if typ == "text":
                    content_blocks.append({**block})

                elif typ == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": json.dumps(
                                    block.get("input", {}),
                                    ensure_ascii=False,
                                ),
                            },
                        },
                    )

                elif typ == "tool_result":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("id"),
                            "content": str(block.get("output")),
                            "name": block.get("name"),
                        },
                    )

                elif typ == "image":
                    content_blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": _to_openai_image_url(
                                    str(block.get("url")),
                                ),
                            },
                        },
                    )

                else:
                    logger.warning(
                        f"Unsupported block type {typ} in the message, "
                        f"skipped.",
                    )

            msg_openai = {
                "role": msg.role,
                "name": msg.name,
                "content": content_blocks or None,
            }

            if tool_calls:
                msg_openai["tool_calls"] = tool_calls

            # When both content and tool_calls are None, skipped
            if msg_openai["content"] or msg_openai.get("tool_calls"):
                messages.append(msg_openai)

        return messages

    @classmethod
    def format_tools_json_schemas(cls, schemas: dict[str, dict]) -> list[dict]:
        """Format the JSON schemas of the tool functions to the format that
        OpenAI API expects. This function will take the parsed JSON schema
        from `agentscope.service.ServiceToolkit` as input and return
        the formatted JSON schema.

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

        Args:
            schemas (`dict[str, dict]`):
                The JSON schema of the tool functions.

        Returns:
            `list[dict]`:
                The formatted JSON schema.
        """

        # The schemas from ServiceToolkit is exactly the same with the format
        # that OpenAI API expects, so we just return the input schemas.

        assert isinstance(
            schemas,
            dict,
        ), f"Expect dict of schemas, got {type(schemas)}."

        for schema in schemas.values():
            assert isinstance(
                schema,
                dict,
            ), f"Expect dict schema, got {type(schema)}."

            assert (
                "type" in schema and "function" in schema
            ), f"Invalid schema: {schema}, expect keys 'type' and 'function'."

            assert (
                schema["type"] == "function"
            ), f"Invalid schema type: {schema['type']}, expect 'function'."

            assert "name" in schema["function"], (
                f"Invalid schema: {schema}, "
                f"expect key 'name' in 'function' field."
            )

        return list(schemas.values())
