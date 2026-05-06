# -*- coding: utf-8 -*-
"""The formatter module."""
import base64
import mimetypes
import tempfile
from abc import abstractmethod
from fnmatch import fnmatch
from typing import Any, List, AsyncGenerator

import shortuuid
from pydantic import BaseModel

from ..message import (
    Msg,
    DataBlock,
    TextBlock,
    URLSource,
    Base64Source,
)


class FormatterBase(BaseModel):
    """The base class for formatters."""

    supported_input_media_types: list[str]
    """The supported media types for multimodal data in the input messages,
    supporting mime types like "image/*", "image/png", "audio/*", "video/*",
     etc."""

    @abstractmethod
    async def format(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Format the Msg objects to a list of dictionaries that satisfy the
        API requirements."""

    @staticmethod
    def assert_list_of_msgs(msgs: list[Msg]) -> None:
        """Assert that the input is a list of Msg objects.

        Args:
            msgs (`list[Msg]`):
                A list of Msg objects to be validated.
        """
        if not isinstance(msgs, list):
            raise TypeError("Input must be a list of Msg objects.")

        for msg in msgs:
            if not isinstance(msg, Msg):
                raise TypeError(
                    f"Expected Msg object, got {type(msg)} instead.",
                )

    def convert_tool_result_to_string(
        self,
        output: str | List[TextBlock | DataBlock],
    ) -> tuple[str, list[TextBlock | DataBlock]]:
        """Turn the tool result list into a textual output to be compatible
        with the LLM API that doesn't support multimodal data in the tool
        result.

        For URL-based images, the URL is included in the list. For
        base64-encoded images, the local file path where the image is saved
        is included in the returned list.

        Args:
            output (`str | List[TextBlock | DataBlock]`):
                The output of the tool response, including text and multimodal
                data like images and audio.

        Returns:
            `tuple[str, list[TextBlock | DataBlock]]`:
                A tuple containing the textual representation of the tool
                result and a list of blocks to be promoted as a user message.
        """

        if isinstance(output, str):
            return output, []

        textual_output = []
        multimodal_data: list = []

        for block in output:
            if isinstance(block, TextBlock):
                textual_output.append(block.text)

            elif isinstance(block, DataBlock):
                main_type = block.source.media_type.split("/")[0]

                if any(
                    fnmatch(block.source.media_type, _)
                    for _ in self.supported_input_media_types
                ):
                    # If supported, promote the block

                    # Create an identifier for such multimodal data for
                    # accurate reference (in terms of order, position, etc.)
                    identifier = shortuuid.uuid()

                    textual_output.append(
                        f"<system-reminder>A(n) {main_type} file is returned "
                        f"and will be presented to you with the identifier "
                        f"[{identifier}].</system-reminder>",
                    )
                    multimodal_data.extend(
                        [
                            TextBlock(
                                text=f"- {identifier} ({main_type} file): ",
                            ),
                            block,
                        ],
                    )
                    multimodal_data.append(block)

                # For unsupported media types, if it's a URL, include it in
                # the textual output; if it's base64 data, save it locally
                # and include the file path in the textual output.
                # Note if you don't want to save the local file, you should
                # transform the base64 data in the tool execution hook
                # rather than changing the formatter.
                elif isinstance(block.source, URLSource):
                    textual_output.append(
                        f"<system-reminder>A(n) {main_type} file is returned "
                        f"and can be accessed at the URL: {block.source.url}."
                        f"</system-reminder>",
                    )

                elif isinstance(block.source, Base64Source):
                    # Have to save the base64 data locally
                    extension = mimetypes.guess_extension(
                        block.source.media_type,
                    )
                    with tempfile.NamedTemporaryFile(
                        suffix=extension,
                        delete=False,
                    ) as temp_file:
                        decoded_data = base64.b64decode(block.source.data)
                        temp_file.write(decoded_data)
                        textual_output.append(
                            f"<system-reminder>A(n) {main_type} file is "
                            f"returned and saved locally at: {temp_file.name}."
                            f"</system-reminder>",
                        )

        # Add system reminder tags if there is multimodal data to be promoted
        if multimodal_data:
            multimodal_data = [
                TextBlock(
                    text="<system-reminder>The multimodal data and their "
                    "identifiers are listed as follows:",
                ),
                *multimodal_data,
                TextBlock(
                    text="</system-reminder>",
                ),
            ]

        return "\n".join(textual_output), multimodal_data

    @staticmethod
    async def _group_messages(msgs: list[Msg]) -> AsyncGenerator:
        """Group messages into tool sequences and agent messages.

        Args:
            msgs (`list[Msg]`):
                A list of Msg objects to be grouped.
        """
        group_type = None
        group = []
        for msg in msgs:
            if group_type is None:
                if msg.get_content_blocks(
                    "tool_call",
                ) or msg.get_content_blocks("tool_result"):
                    group_type = "tool_sequence"
                else:
                    group_type = "agent_message"
                group.append(msg)
                continue

            if group_type == "tool_sequence":
                if msg.has_content_blocks(
                    "tool_call",
                ) or msg.has_content_blocks("tool_result"):
                    group.append(msg)
                else:
                    yield group_type, group
                    group = [msg]
                    group_type = "agent_message"

            elif group_type == "agent_message":
                if msg.has_content_blocks(
                    "tool_call",
                ) or msg.has_content_blocks("tool_result"):
                    yield group_type, group
                    group = [msg]
                    group_type = "tool_sequence"
                else:
                    group.append(msg)

        if group_type:
            yield group_type, group
