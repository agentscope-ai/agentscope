# -*- coding: utf-8 -*-
"""The base class for message unit"""
import datetime
import json
import uuid
from typing import (
    Literal,
    Union,
    List,
    Optional,
    Dict,
    Any,
    Sequence,
)

from loguru import logger
from pydantic import BaseModel, Field

from .block import (
    ContentBlock,
    TextBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
    FileBlock,
)
from ..utils.common import (
    _guess_type_by_extension,
)


JSONSerializable = Union[
    str,
    int,
    float,
    bool,
    None,
    List[Any],
    Dict[str, Any],
]


class Msg(BaseModel):
    """The message class in AgentScope."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The unique identity of the message."""
    name: str
    """The name of the message sender."""
    role: Literal["system", "user", "assistant"]
    """The role of the message sender."""
    content: Union[str, list[ContentBlock]]
    """The content of the message."""
    metadata: JSONSerializable = Field(default=None)
    """The additional metadata stored in the message."""
    timestamp: str = Field(
        default_factory=lambda: datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S",
        ),
    )
    """The timestamp of the message."""

    def __init__(  # pylint: disable=too-many-branches
        self,
        name: str,
        content: Union[str, Sequence[ContentBlock], None],
        role: Literal["system", "user", "assistant"],
        metadata: JSONSerializable = None,
        echo: bool = False,
        url: Optional[Union[str, List[str]]] = None,
    ) -> None:
        """Initialize the message object.

        There are two ways to initialize a message object:
        - Providing `name`, `content`, `role`, `url`(Optional),
          `metadata`(Optional) to initialize a normal message object.
        - Providing `host`, `port`, `task_id` to initialize a placeholder.

        The initialization of message has a high priority, which means that
        when `name`, `content`, `role`, `host`, `port`, `task_id` are all
        provided, the message will be initialized as a normal message object
        rather than a placeholder.

        Args:
            name (`str`):
                The name of who generates the message.
            content (`Union[str, list[ContentBlock], None]`):
                The content of the message. If `None` provided, the content
                will be initialized as an empty list.
            role (`Union[str, Literal["system", "user", "assistant"]]`):
                The role of the message sender.
            metadata (`Optional[Union[dict, str]]`, defaults to `None`):
                The additional information stored in the message.
            echo (`bool`, defaults to `False`):
                Whether to print the message when initializing the message obj.
            url (`Optional[Union[str, List[str]]`, defaults to `None`):
                The url of the message.
        """
        if content is None:
            content = []

        if not isinstance(content, (str, list)):
            logger.warning(
                f"The content should be a string or a list of content blocks."
                f"The input content {type(content)} is converted to a string "
                f"automatically.",
            )
            content = str(content)

        # Deal with the deprecated url argument
        if url is not None:
            logger.error(
                "The url argument will be deprecated in the future. Consider "
                "using the ContentBlock instead to attach files to the "
                "message",
            )

            if isinstance(content, str):
                content = [
                    TextBlock(type="text", text=content),
                ]

            if isinstance(url, str):
                url = [url]

            for _ in url:
                typ = _guess_type_by_extension(_)
                if typ == "image":
                    content.append(
                        ImageBlock(
                            type="image",
                            url=_,
                        ),
                    )
                elif typ == "audio":
                    content.append(
                        AudioBlock(
                            type="audio",
                            url=_,
                        ),
                    )
                elif typ == "video":
                    content.append(
                        VideoBlock(
                            type="video",
                            url=_,
                        ),
                    )
                else:
                    content.append(
                        FileBlock(
                            type="file",
                            url=_,
                        ),
                    )

        # Check if the metadata is JSON serializable
        if metadata is not None:
            try:
                json.dumps(metadata)
            except Exception as e:
                raise TypeError(
                    "The metadata should be JSON serializable, "
                    f"got {type(metadata)}.",
                ) from e

        super().__init__(
            name=name,
            content=content,
            role=role,
            metadata=metadata,
        )

        if echo:
            if hasattr(logger, "chat"):
                logger.chat(self)
            else:
                logger.info(self)

    def to_dict(self) -> dict:
        """Serialize the message into a dictionary, which can be
        deserialized by calling the `from_dict` function.

        Returns:
            `dict`: The serialized dictionary.
        """
        serialized_dict = {
            "__module__": self.__class__.__module__,
            "__name__": self.__class__.__name__,
        }

        attrs = self.model_dump()

        return {**serialized_dict, **attrs}

    @classmethod
    def from_dict(cls, serialized_dict: dict) -> "Msg":
        """Deserialize the dictionary to a Msg object.

        Args:
            serialized_dict (`dict`):
                A dictionary that must contain the keys

        Returns:
            `Msg`: A Msg object.
        """

        assert serialized_dict.pop("__module__") == cls.__module__
        assert serialized_dict.pop("__name__") == cls.__name__

        id_attr = serialized_dict.pop("id")
        timestamp_attr = serialized_dict.pop("timestamp")

        msg = Msg.model_validate(
            serialized_dict,
        )

        msg.id = id_attr
        msg.timestamp = timestamp_attr
        return msg

    def get_text_content(self) -> Union[str, None]:
        """Get the pure text blocks from the message content."""
        if isinstance(self.content, str):
            return self.content

        gathered_text = None
        for block in self.content:
            if block.get("type") == "text":
                if gathered_text is None:
                    gathered_text = str(block.get("text"))
                else:
                    gathered_text += block.get("text")
        return gathered_text

    def get_content_blocks(
        self,
        block_type: Optional[
            Literal[
                "text",
                "tool_use",
                "tool_result",
                "image",
                "audio",
                "video",
                "file",
            ]
        ] = None,
    ) -> list[ContentBlock]:
        """Get the content in block format. If the content is a string,
        it will be converted to a text block.

        Args:
            block_type (`Optional[Literal["text", "tool_use", "tool_result",
                "image", "audio", "video", "file"]]`, defaults to `None`):
                The type of the block to be extracted. If `None`, all blocks
                will be returned.

        Returns:
            `list[ContentBlock]`:
                The content blocks.
        """
        blocks: list[ContentBlock] = []
        if isinstance(self.content, str):
            blocks.append(
                TextBlock(type="text", text=self.content),
            )
        else:
            blocks = self.content

        if block_type is not None:
            blocks = [_ for _ in blocks if _["type"] == block_type]

        return blocks
