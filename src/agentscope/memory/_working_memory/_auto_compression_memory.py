# -*- coding: utf-8 -*-
""""""
from enum import Enum
from typing import Any, Type, Literal

from pydantic import BaseModel, Field

from agentscope.memory._working_memory._memory_base import MemoryBase
from agentscope.memory._storage import MemoryStorageBase, InMemoryMemoryStorage
from agentscope.formatter import FormatterBase
from agentscope.message import Msg
from agentscope.model import ChatModelBase
from agentscope.token import TokenCounterBase


class CompressedMemoryModel(BaseModel):
    """The compressed memory model, used to generate summary of old memories."""

    task_overview: str = Field(
        max_length=300,
        description=(
            "The user's core request and success criteria.\n"
            "Any clarifications or constraints they specified"
        )
    )
    current_state: str = Field(
        max_length=300,
        description=(
            "What has been completed so far.\n"
            "File created, modified, or analyzed (with paths if relevant).\n"
            "Key outputs or artifacts produced."
        )
    )
    important_discoveries: str = Field(
        max_length=300,
        description=(
            "Technical constraints or requirements uncovered.\n"
            "Decisions made and their rationale.\n"
            "Errors encountered and how they were resolved.\n"
            "What approaches were tried that didn't work (and why)"
        )
    )
    next_steps: str = Field(
        max_length=200,
        description=(
            "Specific actions needed to complete the task.\n"
            "Any blockers or open questions to resolve.\n"
            "Priority order if multiple steps remain"
        )
    )
    context_to_preserve: str = Field(
        max_length=300,
        description=(
            "User preferences or style requirements.\n"
            "Domain-specific details that aren't obvious.\n"
            "Any promises made to the user"
        )
    )

    """<system-info>The following is a compressed memory summary due to limited context length. You can learn about the previous conversation from it.
For more detailed conversations, you can use the xxx tools to retrieve them if needed.
<summary>
</summary>
</system-info>"""


class CompressionConfig(BaseModel):
    """The compression related configuration in AgentScope"""

    agent_token_counter: TokenCounterBase
    model: ChatModelBase
    formatter: FormatterBase
    trigger_threshold: int

    keep_recent: int = 10,
    agent_formatter: FormatterBase | None = None
    compression_prompt: str | None = None
    compression_summary_template: str | None = None
    compression_summary_model: Type[BaseModel] = CompressedMemoryModel
    compression_timing: Literal[
        "after_add",
        "before_get_memory",
    ] = "before_get_memory"


class AutoCompressionMemory(MemoryBase):
    """The database memory class with auto compression capability."""

    UNCOMPRESSED_MARK: str = "uncompressed"
    """The mark used to label the uncompressed messages."""

    _DEFAULT_COMPRESSION_PROMPT: str = (
        f"""You have been working on the task described above but have not yet completed it. Write a continuation summary that will allow you (or another instance of yourself) to resume work efficiently in a future context window where the conversation history will be replaced with this summary. Your summary should be structured, concise, and actionable. 

You should generate the summarized for this."""
    )


    def __init__(
        self,
        compression_config: CompressionConfig | None = None,
        memory_storage: MemoryStorageBase | None = None,
    ) -> None:
        """Initialize the database memory object.

        Args:
            compression_config (`CompressionConfig | None`, optional):
                The compression configuration. If provided the auto
                compression will be activated.
            memory_storage (`MemoryStorage | None`, optional):
                The memory storage instance.
        """
        super().__init__()

        # The summary of the compressed memory
        self._compressed_summary: str = ""
        self.memory_storage = memory_storage or InMemoryMemoryStorage()

        # Treat the _compressed_summary as state
        self.register_state("_compressed_summary")
        self.compression_config = compression_config

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        allow_duplicates: bool = False,
    ) -> None:
        """Add message into the memory.

        Args:
            memories (`Msg | list[Msg] | None`):
                The message to add.
            allow_duplicates (`bool`, defaults to `False`):
                If allow adding duplicate messages (with the same id) into
                the memory.
        """
        if memories is None:
            return

        if isinstance(memories, Msg):
            memories = [memories]

        if not isinstance(memories, list):
            raise TypeError(
                f"The memories should be a list of Msg or a single Msg, "
                f"but got {type(memories)}.",
            )

        for msg in memories:
            if not isinstance(msg, Msg):
                raise TypeError(
                    f"The memories should be a list of Msg or a single Msg, "
                    f"but got {type(msg)}.",
                )

        # Add the messages with uncompressed mark
        await self.memory_storage.add_message(
            msg=memories,
            mark=self.UNCOMPRESSED_MARK,
        )

        # Trigger compression if configured to do so after adding
        if self.compression_config and  self.compression_config.compression_timing == "after_add":
            await self._trigger_compression()

    async def get_memory(self) -> list[Msg]:
        """Get the messages from the memory. If compression is enabled, the
        compressed summary will be attached as the first message, and only the
        uncompressed messages will be returned. Otherwise, all messages will
        be returned.

        Returns:
            `list[Msg]`:
                The list of messages from the memory.
        """
        # Trigger compression if configured to do so before getting memory
        if self.compression_config and self.compression_config.compression_timing == "before_get_memory":
            await self._trigger_compression()

        # Get the messages
        msgs = await self.memory_storage.get_messages(
            # Only get the uncompressed messages if compression is enabled.
            # Otherwise, get all messages.
            mark=self.UNCOMPRESSED_MARK if self.compression_config else None,
        )

        if self._compressed_summary:
            # Attach the compressed summary as the first message
            return [
                Msg(
                    "user",
                    f"<system-info>{self._compressed_summary}</system-info>",
                    "user",
                )
            ] + msgs

        return msgs

    async def delete(self, ids: list[int]) -> None:
        """Delete the specified item by index(es).

        Args:
            ids (`list[int]`):
                The list of IDs to delete.
        """
        await self.memory_storage.remove_messages(msg_ids=ids)

    async def clear(self) -> None:
        """Clear the memory content."""
        self._compressed_summary = ""
        await self.memory_storage.clear()

    async def size(self) -> int:
        """Get the number of messages in the memory."""
        cnt = await self.memory_storage.size(
            mark=self.UNCOMPRESSED_MARK,
        )
        if self._compressed_summary:
            cnt += 1
        return cnt

    async def _trigger_compression(self) -> None:
        """Trigger the compression process to summarize old memories."""
        if not self.compression_config:
            return

        # Obtain all the uncompressed messages
        uncompressed_msgs = await self.memory_storage.get_messages(
            mark=self.UNCOMPRESSED_MARK,
        )

        if not uncompressed_msgs:
            return

        if self._compressed_summary:
            uncompressed_msgs.insert(
                0,
                Msg(
                    "user",
                    f"<system-info>{self._compressed_summary}</system-info>",
                    "user",
                )
            )

        # If we need to compress the memories
        formatted_msgs = await self.compression_config.agent_formatter.format(
            uncompressed_msgs,
        )

        msgs_len = await self.compression_config.agent_token_counter.count(
            formatted_msgs,
        )

        if msgs_len > self.compression_config.compression_trigger_threshold:

            # 计算要压缩多少的内容，即多少条msg才能达到 target threshold
            compression_len = msgs_len - self.compression_config.compression_target_threshold



            compression_prompt = await self.compression_config.compression_formatter.format(
                [
                    Msg(
                        "user",
                        self._DEFAULT_COMPRESSION_PROMPT or self.compression_config.compression_prompt,
                        "user",
                    ),
                ]
            )
            # trigger the compression
            res = await self.compression_config.compression_model(
                compression_prompt,
            )

            last_chunk = None
            if self.compression_config.compression_model.stream:
                async for chunk in res:
                    last_chunk = chunk
            else:
                last_chunk = res

            compressed_summary = last_chunk.content


