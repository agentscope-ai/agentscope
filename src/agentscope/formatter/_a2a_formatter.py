# -*- coding: utf-8 -*-
"""The A2A message formatter class."""
import mimetypes
import uuid
from typing import Literal, TYPE_CHECKING


from .._logging import logger
from ._formatter_base import FormatterBase
from ..message import (
    Msg,
    TextBlock,
    URLSource,
    Base64Source,
    ContentBlock,
)


if TYPE_CHECKING:
    from a2a.types import (
        Message,
        Task,
        Part,
    )
else:
    Message = "a2a.types.Message"
    Task = "a2a.types.Task"
    Part = "a2a.types.Part"


class A2AChatFormatter(FormatterBase):
    """A2A message formatter class, which convert AgentScope messages into
    A2A message format."""

    async def format(self, msgs: list[Msg]) -> Message:
        """Convert AgentScope messages into a A2A message object. Note that
        A2A server only supports single request message, so the input msgs
        list will be merged into a single A2A Message.

        .. note:: Note the A2A protocol receives a single message per request,
         so multi-message inputs will be merged into one A2A Message with role
         'user'.

        Args:
            msgs (`list[Msg]`):
                List of AgentScope Msg objects to be converted.

        Returns:
            `Message`:
                The converted A2A Message object.
        """

        from a2a.types import (
            Part,
            TextPart,
            FilePart,
            FileWithUri,
            FileWithBytes,
            DataPart,
            Role,
            Message,
        )

        self.assert_list_of_msgs(msgs)

        parts = []
        for msg in msgs:
            for block in msg.get_content_blocks():
                block_type = block.get("type")
                if block_type == "text" and block.get("text"):
                    parts.append(
                        Part(
                            root=TextPart(
                                text=block.get("text"),
                            ),
                        ),
                    )

                elif block_type == "thinking" and block.get("thinking"):
                    parts.append(
                        Part(
                            root=TextPart(
                                text=block.get("thinking"),
                            ),
                        ),
                    )

                elif block_type in [
                    "image",
                    "video",
                    "audio",
                ] and block.get("source"):
                    source = block.get("source", {})
                    source_type = source.get("type")

                    if source_type == "url":
                        parts.append(
                            Part(
                                root=FilePart(
                                    file=FileWithUri(
                                        uri=source.get("url"),
                                    ),
                                ),
                            ),
                        )

                    elif source_type == "base64":
                        parts.append(
                            Part(
                                root=FilePart(
                                    file=FileWithBytes(
                                        bytes=source.get("data"),
                                        mime_type=source.get("media_type"),
                                    ),
                                ),
                            ),
                        )

                    else:
                        raise ValueError(
                            f"Unsupported source type: {source_type}",
                        )

                elif block_type in ["tool_use", "tool_result"]:
                    parts.append(
                        Part(
                            root=DataPart(
                                data=block,
                            ),
                        ),
                    )

                else:
                    logger.error(
                        "Unsupported block type %s in A2AFormatter.",
                        block_type,
                    )

        a2a_message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.user,
            parts=parts,
        )

        return a2a_message

    async def format_a2a_message(self, name: str, message: Message) -> Msg:
        """Convert A2A Message object back to AgentScope Msg format.

        Args:
            name (`str`):
                The name of the message sender.
            message (`Message`):
                The A2A Message object to be converted.

        Returns:
            `list[Msg]`:
                List of converted AgentScope Msg objects.
        """

        from a2a.types import Role

        content = []
        metadata = None
        for part in message.parts:
            content.append(
                await self._format_a2a_part(part),
            )

        if message.role == Role.user:
            role: Literal["user", "assistant"] = "user"
        elif message.role == Role.agent:
            role = "assistant"
        else:
            raise ValueError(
                f"Unsupported role: {message.role} in A2A message.",
            )

        return Msg(
            name=name,
            role=role,
            content=content,
            metadata=metadata,
        )

    @staticmethod
    def _guess_type(
        uri: str | None = None,
        mime_type: str | None = None,
    ) -> Literal["image", "video", "audio", "unknown"]:
        """Guess the content type from the uri or mime type.

        Args:
            uri (`str | None`, optional):
                The uri of the content.
            mime_type (`str | None`, optional):
                The mime type of the content.

        Returns:
            `Literal["image", "video", "audio", "unknown"]`:
                The guessed content type.
        """
        if mime_type is None and uri is None:
            raise ValueError(
                "Either uri or mime_type must be provided to guess the"
                " content type.",
            )

        if mime_type is None:
            mime_type, _encoding = mimetypes.guess_type(uri or "")

        if isinstance(mime_type, str):
            if mime_type.startswith("image/"):
                return "image"

            if mime_type.startswith("video/"):
                return "video"

            if mime_type.startswith("audio/"):
                return "audio"

        return "unknown"

    async def format_a2a_task(self, name: str, task: Task) -> list[Msg]:
        """Convert A2A Task object back to AgentScope Msg format.

        Args:
            name (`str`):
                The name of the message sender.
            task (`Task`):
                The A2A Task object to be converted.

        Returns:
            `list[Msg]`:
                Converted AgentScope Msg objects.
        """
        msgs = []
        if task.status and task.status.message:
            msgs.append(
                await self.format_a2a_message(name, task.status.message),
            )

        merged_msgs = []
        for msg in msgs:
            if merged_msgs and merged_msgs[-1].role == msg.role:
                merged_msgs[-1].content.extend(msg.content)

            else:
                merged_msgs.append(msg)

        if task.artifacts:
            for artifact in task.artifacts:
                artifact_content = [
                    await self._format_a2a_part(_) for _ in artifact.parts
                ]

                if merged_msgs and merged_msgs[-1].role == "assistant":
                    merged_msgs[-1].content.extend(artifact_content)
                    merged_msgs[-1].metadata = artifact.metadata

                else:
                    merged_msgs.append(
                        Msg(
                            name=name,
                            role="assistant",
                            content=artifact_content,
                            metadata=artifact.metadata,
                        ),
                    )

        return merged_msgs

    async def _format_a2a_part(self, part: Part) -> ContentBlock:
        """Convert a single A2A Part object into AgentScope ContentBlock.

        .. note:: We will try to convert the `DataPart` into tool use and tool
         result blocks if possible.

        Args:
            part (`Part`):
                The A2A Part object to be converted.

        Returns:
            `ContentBlock`:
                The converted AgentScope ContentBlock.
        """

        from a2a.types import (
            TextPart,
            FilePart,
            FileWithUri,
            FileWithBytes,
            DataPart,
        )

        if isinstance(part.root, TextPart):
            return TextBlock(
                type="text",
                text=part.root.text,
            )

        if isinstance(part.root, FilePart):
            if isinstance(part.root.file, FileWithUri):
                return {  # type: ignore[return-value, misc]
                    "type": self._guess_type(
                        part.root.file.uri,
                        part.root.file.mime_type,
                    ),
                    "source": URLSource(
                        type="url",
                        url=part.root.file.uri,
                    ),
                }

            if isinstance(part.root.file, FileWithBytes):
                return {  # type: ignore[return-value, misc]
                    "type": self._guess_type(
                        mime_type=part.root.file.mime_type,
                    ),
                    "source": Base64Source(
                        type="base64",
                        media_type=part.root.file.mime_type
                        or "application/octet-stream",
                        data=part.root.file.bytes,
                    ),
                }

            raise ValueError(
                f"Unsupported File type: {type(part.root.file)} in A2A"
                "message.",
            )

        if isinstance(part.root, DataPart):
            # Maybe the tool use and tool result blocks
            if {
                "type",
                "name",
                "input",
                "id",
            } <= part.root.data.keys() and part.root.data[
                "type"
            ] == "tool_use":
                return part.root.data

            if {
                "type",
                "name",
                "output",
                "id",
            } <= part.root.data.keys() and part.root.data[
                "type"
            ] == "tool_result":
                return part.root.data

            # TODO: what about the other data parts?
            return TextBlock(
                type="text",
                text=str(part.root.data),
            )

        raise ValueError(
            f"Unsupported Part type: {type(part.root)} in A2A message"
            f": {part.root}",
        )
