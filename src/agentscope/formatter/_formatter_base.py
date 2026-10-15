# -*- coding: utf-8 -*-
"""The formatter module."""
import base64
import mimetypes
import tempfile
from abc import abstractmethod
from fnmatch import fnmatch
from typing import Any, AsyncGenerator, Awaitable, Callable, List

from pydantic import BaseModel, Field

from ..message import (
    Msg,
    DataBlock,
    HintBlock,
    TextBlock,
    URLSource,
)


class FormatterBase(BaseModel):
    """The base class for formatters."""

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        description=(
            "The supported input types, aligned with the model card's "
            "``input_types`` field. Entries other than ``text/plain`` and "
            "``application/x-thinking`` are treated as media-type patterns "
            "(glob-style, e.g. ``image/*``, ``audio/mp3``) that control which "
            "``DataBlock``\\s are forwarded to the API."
        ),
    )
    """The supported input types for this formatter, aligned with the model
    card's ``input_types`` field."""

    @property
    def supported_input_media_types(self) -> list[str]:
        """Derive the accepted media-type patterns from :attr:`input_types` by
        excluding ``text/plain`` and ``application/x-thinking``."""
        return [
            t
            for t in self.input_types
            if t not in ("text/plain", "application/x-thinking")
        ]

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

    @staticmethod
    def _convert_unsupported_data_block_to_string(block: DataBlock) -> str:
        """Convert an unsupported data block into its textual fallback.

        URL sources remain accessible through their URL. Base64 sources are
        persisted to a temporary file so the model can still reference the
        result when the target API cannot carry that media type directly.

        Args:
            block (`DataBlock`):
                The unsupported data block to convert.

        Returns:
            `str`:
                The textual fallback for the data block.
        """
        main_type = block.source.media_type.split("/")[0]

        if isinstance(block.source, URLSource):
            return (
                f"<system-reminder>A(n) {main_type} file is returned "
                f"and can be accessed at the URL: {block.source.url}."
                f"</system-reminder>"
            )

        extension = mimetypes.guess_extension(block.source.media_type)
        with tempfile.NamedTemporaryFile(
            suffix=extension,
            delete=False,
        ) as temp_file:
            decoded_data = base64.b64decode(block.source.data)
            temp_file.write(decoded_data)
            return (
                f"<system-reminder>A(n) {main_type} file is returned and "
                f"saved locally at: {temp_file.name}.</system-reminder>"
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

                    # Reuse the block's own stable id as the identifier so
                    # repeated formatting of the same unchanged Msg history
                    # (e.g. on every LLM call) produces the same text, rather
                    # than a fresh random id each time.
                    identifier = block.id

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

                # For unsupported media types, retain the existing textual
                # fallback rather than silently dropping the result.
                else:
                    textual_output.append(
                        self._convert_unsupported_data_block_to_string(block),
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

    @staticmethod
    def _split_agent_messages_at_hints(
        msgs: list[Msg],
    ) -> list[tuple[bool, list[Msg]]]:
        """Split agent messages around hints while preserving message order.

        Args:
            msgs (`list[Msg]`):
                The non-tool messages in one agent-message group.

        Returns:
            `list[tuple[bool, list[Msg]]]`:
                Ordered segments. The boolean is ``True`` for a segment that
                contains a single ``HintBlock`` and ``False`` for history
                segments. Messages without hints are returned unchanged as a
                single history segment.
        """
        if not any(
            isinstance(block, HintBlock)
            for msg in msgs
            for block in msg.get_content_blocks()
        ):
            return [(False, msgs)] if msgs else []

        segments: list[tuple[bool, list[Msg]]] = []
        history_messages: list[Msg] = []

        for msg in msgs:
            history_blocks = []
            for block in msg.get_content_blocks():
                if isinstance(block, HintBlock):
                    if history_blocks:
                        history_messages.append(
                            msg.model_copy(update={"content": history_blocks}),
                        )
                        history_blocks = []

                    if history_messages:
                        segments.append((False, history_messages))
                        history_messages = []

                    segments.append(
                        (
                            True,
                            [msg.model_copy(update={"content": [block]})],
                        ),
                    )
                else:
                    history_blocks.append(block)

            if history_blocks:
                history_messages.append(
                    msg.model_copy(update={"content": history_blocks}),
                )

        if history_messages:
            segments.append((False, history_messages))

        return segments

    async def _format_agent_message_with_hints(
        self,
        msgs: list[Msg],
        is_first: bool,
        format_agent_message: Callable[
            [list[Msg], bool],
            Awaitable[list[Any]],
        ],
    ) -> tuple[list[Any], bool]:
        """Format history and route each hint through its chat formatter.

        Args:
            msgs (`list[Msg]`):
                The non-tool messages in one agent-message group.
            is_first (`bool`):
                Whether the first history segment should include its prompt.
            format_agent_message (`Callable`):
                The provider-specific history formatter.

        Returns:
            `tuple[list[Any], bool]`:
                The formatted messages and whether any history was emitted.
                Hints do not consume the first-history prompt.
        """
        formatted_messages: list[Any] = []
        has_history = False

        for is_hint, segment in self._split_agent_messages_at_hints(msgs):
            if is_hint:
                formatted_messages.extend(
                    await self._format_hint_message(segment),
                )
                continue

            formatted_history = await format_agent_message(segment, is_first)
            formatted_messages.extend(formatted_history)
            if formatted_history:
                is_first = False
                has_history = True

        return formatted_messages, has_history

    async def _format_hint_message(self, msgs: list[Msg]) -> list[Any]:
        """Format a HintBlock message through the provider's chat formatter.

        Multi-agent formatters that delegate tool sequences to a chat
        formatter can reuse that path. Providers with a different delegation
        override this method.

        Args:
            msgs (`list[Msg]`):
                One message containing a single ``HintBlock``.

        Returns:
            `list[Any]`:
                The provider-specific user-message representation.
        """
        raise RuntimeError(
            "A provider chat formatter is required for HintBlock messages.",
        )
