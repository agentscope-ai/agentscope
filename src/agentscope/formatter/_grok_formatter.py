# -*- coding: utf-8 -*-
"""The xAI Grok formatter module.

This formatter converts AgentScope ``Msg`` objects into the protobuf
``Message`` objects expected by the ``xai_sdk`` gRPC client.  Unlike every
other formatter, the ``format()`` method returns a list of
``chat_pb2.Message`` proto objects rather than plain dicts, because the
``xai_sdk`` chat API accepts proto messages directly.
"""
from typing import Any, List

from pydantic import Field

from . import FormatterBase
from .._logging import logger
from ..message import (
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    DataBlock,
    URLSource,
    Base64Source,
    HintBlock,
)


class GrokChatFormatter(FormatterBase):
    """Formatter for the xAI Grok chat model.

    Converts ``Msg`` objects into ``xai_sdk`` protobuf ``Message`` objects
    that can be appended directly to a ``xai_sdk`` chat session.

    Unlike other formatters whose ``format()`` returns ``list[dict]``, this
    formatter returns ``list[chat_pb2.Message]``.  The type annotation is
    intentionally widened to ``list[Any]`` to accommodate this difference.
    """

    supported_input_media_types: list[str] = Field(
        default=["image/jpeg", "image/png"],
        description="The image MIME types supported by Grok vision models.",
    )
    """Supported image media types for multimodal inputs."""

    async def format(
        self,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> List[Any]:
        """Convert a list of ``Msg`` objects to ``xai_sdk`` proto messages.

        Args:
            msgs (`list[Msg]`):
                A list of ``Msg`` objects representing the conversation.
            **kwargs (`Any`):
                Unused; retained for interface compatibility.

        Returns:
            `list[Any]`:
                A list of ``chat_pb2.Message`` proto objects, ready to be
                appended to a ``xai_sdk`` chat session via
                ``chat.append()``.
        """
        from xai_sdk.chat import (
            assistant,
            image,
            system,
            tool_result,
            user,
            chat_pb2,
        )

        self.assert_list_of_msgs(msgs)

        xai_messages: List[Any] = []

        for msg in msgs:
            blocks = msg.get_content_blocks()

            text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
            tool_call_blocks = [
                b for b in blocks if isinstance(b, ToolCallBlock)
            ]
            tool_result_blocks = [
                b for b in blocks if isinstance(b, ToolResultBlock)
            ]

            if msg.role == "system":
                text = "\n".join(b.text for b in text_blocks)
                xai_messages.append(system(text))

            elif msg.role == "user":
                content_args: list = []
                for block in blocks:
                    if isinstance(block, (HintBlock, ThinkingBlock)):
                        pass
                    elif isinstance(block, TextBlock):
                        content_args.append(block.text)
                    elif isinstance(block, DataBlock):
                        if block.source.media_type.startswith("image/"):
                            if isinstance(block.source, URLSource):
                                content_args.append(image(block.source.url))
                            elif isinstance(block.source, Base64Source):
                                content_args.append(
                                    image(
                                        f"data:{block.source.media_type};"
                                        f"base64,{block.source.data}",
                                    ),
                                )
                        else:
                            logger.warning(
                                "Unsupported media type %s for Grok API. "
                                "Only image/jpeg and image/png are supported. "
                                "This block will be skipped.",
                                block.source.media_type,
                            )
                    else:
                        logger.warning(
                            "Unsupported block type %s in user message, "
                            "skipped.",
                            type(block).__name__,
                        )

                if content_args:
                    xai_messages.append(user(*content_args))

            elif msg.role == "assistant":
                if tool_result_blocks:
                    # Convert each ToolResultBlock to a tool_result message.
                    for tr_block in tool_result_blocks:
                        output_text = self._extract_result_text(
                            tr_block.output,
                        )
                        xai_messages.append(
                            tool_result(
                                output_text,
                                tool_call_id=tr_block.id,
                            ),
                        )

                elif tool_call_blocks:
                    # Assistant turn that triggered tool calls (history).
                    msg_proto = chat_pb2.Message()
                    msg_proto.role = chat_pb2.MessageRole.Value(
                        "ROLE_ASSISTANT",
                    )
                    if text_blocks:
                        c = msg_proto.content.add()
                        c.text = "\n".join(b.text for b in text_blocks)
                    for tc in tool_call_blocks:
                        proto_tc = msg_proto.tool_calls.add()
                        proto_tc.id = tc.id
                        proto_tc.type = chat_pb2.ToolCallType.Value(
                            "TOOL_CALL_TYPE_CLIENT_SIDE_TOOL",
                        )
                        proto_tc.function.name = tc.name
                        proto_tc.function.arguments = tc.input
                    xai_messages.append(msg_proto)

                else:
                    # Regular assistant text message.
                    text = "\n".join(b.text for b in text_blocks)
                    if text:
                        xai_messages.append(assistant(text))

            else:
                logger.warning(
                    "Unsupported message role '%s', skipped.",
                    msg.role,
                )

        return xai_messages

    def _extract_result_text(self, output: Any) -> str:
        """Extract a plain-text string from a ``ToolResultBlock`` output.

        Args:
            output (`Any`):
                The raw output of a ``ToolResultBlock``, which may be a
                string, a list of blocks, or another type.

        Returns:
            `str`:
                A plain-text representation of the output.
        """
        if output is None:
            return ""
        if isinstance(output, str):
            return output
        if isinstance(output, list):
            parts = []
            for item in output:
                if isinstance(item, TextBlock):
                    parts.append(item.text)
                elif isinstance(item, str):
                    parts.append(item)
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(output)
