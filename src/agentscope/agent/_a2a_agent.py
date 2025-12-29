# -*- coding: utf-8 -*-
"""A2A agent implementation for AgentScope.

This module provides the A2A (Agent-to-Agent) protocol implementation,
enabling AgentScope agents to communicate with remote agents using the
A2A standard protocol.
"""
from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Literal, Union, Type, TYPE_CHECKING, Optional
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from ._a2a_card_resolver import (
    AgentCardResolverBase,
    FixedAgentCardResolver,
)
from ._agent_base import AgentBase
from .._logging import logger
from ..message import (
    Msg,
    ContentBlock,
    TextBlock,
    ThinkingBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
    ToolUseBlock,
    ToolResultBlock,
    Base64Source,
    URLSource,
)

if TYPE_CHECKING:
    from a2a.client import ClientConfig, ClientFactory, Consumer, Client, BaseClient
    from a2a.client.client_factory import TransportProducer
    from a2a.types import (
        AgentCard,
        Artifact,
        DataPart,
        FilePart,
        Message as A2AMessage,
        Part,
        TaskStatus,
        TaskStatusUpdateEvent,
        TaskArtifactUpdateEvent,
        TransportProtocol,
        PushNotificationConfig,
        Task,
    )
    from grpc.aio import Channel
    from pydantic import BaseModel


@dataclass
class A2aAgentConfig:
    """Configuration for A2A agent client.

    This configuration class contains all settings needed for connecting
    to and communicating with remote A2A agents, including transport
    preferences, streaming options, and custom client configurations.
    """

    streaming: bool = True
    """Whether the client supports streaming responses."""

    polling: bool = False
    """Whether the client prefers to poll for updates from message:send.
    It is the caller's responsibility to check if the response is completed
    and if not, run a polling loop."""

    httpx_client: httpx.AsyncClient | None = None
    """HTTP client to use for connecting to the agent."""

    grpc_channel_factory: Callable[[str], Channel] | None = None
    """Factory function that generates a gRPC connection channel for a
    given URL."""

    supported_transports: list[TransportProtocol | str] = field(
        default_factory=list,
    )
    """Ordered list of supported transports for connecting to the agent,
    in order of preference. An empty list implies JSONRPC only.
    This is a string type to allow custom transports to exist in closed
    ecosystems."""

    use_client_preference: bool = False
    """Whether to use client transport preferences over server preferences.
    It is recommended to use server preferences in most situations."""

    accepted_output_modes: list[str] = field(default_factory=list)
    """The set of accepted output modes for the client."""

    push_notification_configs: list[PushNotificationConfig] = field(
        default_factory=list,
    )
    """List of push notification configurations for the agent."""

    consumers: list[Consumer] = field(default_factory=list)
    """Consumers for handling A2A client events. These intercept
    request/response flows for logging, metrics, and security.
    They are applied automatically to all clients from the factory
    and implement event handlers like on_request_send and
    on_response_receive, enabling modular cross-cutting concerns
    in A2A communications."""

    additional_transport_producers: dict[str, TransportProducer] = field(
        default_factory=dict,
    )
    """Mapping of transport labels to transport producers.
    Used for creating A2A clients with specific transport protocols."""


class A2aAgent(AgentBase):
    """An A2A agent implementation in AgentScope, which supports

    - Communication with remote agents using the A2A protocol
    - Bidirectional message conversion between AgentScope and A2A formats
    - Task lifecycle management with streaming and polling
    - Artifact handling and status tracking
    """

    def __init__(
        self,
        name: str,
        agent_card: Union[AgentCard, AgentCardResolverBase],
        agent_config: A2aAgentConfig = A2aAgentConfig(),
    ) -> None:
        """Initialize the A2A agent.

        Args:
                name (`str`):
                        The name of the agent.
                agent_card (`AgentCard | AgentCardResolverBase`):
                        The agent card or a resolver to obtain the agent
                        card. The agent card contains information about
                        the remote agent, such as its URL and capabilities.
                agent_config (`A2aAgentConfig`, optional):
                        Configuration for the A2A client, including
                        transport preferences and streaming options.
                        Defaults to `A2aAgentConfig()`.
        """
        from a2a.types import AgentCard

        super().__init__()
        self.name: str = name
        """The name of the agent."""

        self._is_ready = False
        """Flag indicating whether the A2A client is initialized."""

        self._agent_card: AgentCard | None = None
        """The resolved agent card."""

        self._a2a_client_factory: ClientFactory | None = None
        """The A2A client factory for creating communication clients."""

        if agent_card is None:
            raise ValueError("Agent card cannot be None")

        if isinstance(agent_card, AgentCardResolverBase):
            self._agent_card_resolver = agent_card
        elif isinstance(agent_card, AgentCard):
            self._agent_card_resolver = FixedAgentCardResolver(agent_card)
        else:
            raise ValueError(
                f"Invalid agent card type: {type(agent_card)}",
            )

        self._agent_config: A2aAgentConfig = agent_config
        """Configuration for the A2A client."""

        self._observed_msgs: list[Msg] = []
        """List of observed messages to be processed in the next reply."""

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Receive the given message(s) without generating a reply.

        The observed messages are stored and will be merged with the
        input messages when `reply` is called. After `reply` completes,
        the stored messages will be cleared.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message(s) to be observed. If None, no action is taken.
        """
        if msg is None:
            return

        if isinstance(msg, Msg):
            self._observed_msgs.append(msg)
        else:
            self._observed_msgs.extend(msg)

        logger.debug(
            "[%s] Observed %d message(s), total stored: %d",
            self.__class__.__name__,
            1 if isinstance(msg, Msg) else len(msg),
            len(self._observed_msgs),
        )

    async def _get_agent_card(self) -> AgentCard:
        """Retrieve the agent card from the configured resolver.

        This method validates the retrieved agent card before using it.
        If validation fails, the previous agent card is retained
        (if available).

        Returns:
                `AgentCard`:
                        The resolved and validated agent card. If the newly
                        resolved card is invalid and a previous valid card
                        exists, the previous card is returned.

        Raises:
                `RuntimeError`:
                        If the resolved agent card is invalid and no
                        previous valid card is available.
        """
        try:
            # Get new agent card from resolver
            new_agent_card = await self._agent_card_resolver.get_agent_card()

            # Validate the new agent card
            await self._validate_agent_card(new_agent_card)

            # If validation passes, update and return
            self._agent_card = new_agent_card
            logger.debug(
                "[%s] Successfully resolved and validated agent card",
                self.__class__.__name__,
            )
            return self._agent_card

        except Exception as e:
            # Validation failed
            logger.warning(
                "[%s] Failed to resolve or validate agent card: %s",
                self.__class__.__name__,
                e,
            )

            # If we have a previous valid agent card, use it
            if self._agent_card is not None:
                logger.debug(
                    "[%s] Using previous valid agent card",
                    self.__class__.__name__,
                )
                return self._agent_card
            else:
                # No previous valid card, must fail
                logger.error(
                    "[%s] No valid agent card available",
                    self.__class__.__name__,
                )
                raise RuntimeError(
                    f"Failed to resolve agent card and "
                    f"no previous valid card available: {e}",
                ) from e

    async def reply(
        self,
        msg: Msg | list[Msg] | None = None,
    ) -> Msg:
        """Send message(s) to the remote A2A agent and receive a response.

        This method merges any previously observed messages with the input
        messages, sends them to the remote agent, and clears the observed
        messages after processing.

        Args:
                msg (`Msg | list[Msg] | None`, optional):
                        The message(s) to send to the remote agent.
                        Can be a single Msg, a list of Msgs, or None.
                        If None, only observed messages will be sent.
                        Defaults to None.

        Returns:
                `Msg`:
                        The response message from the remote agent. For
                        tasks, this may be either a status update message
                        or the final artifacts message, depending on the
                        task state. If no messages are provided (both msg
                        and observed messages are empty), returns a prompt
                        message. If an error occurs during communication,
                        returns an error message.
        """
        from a2a.types import Message as A2AMessage, TaskState

        await self._ensure_ready()

        # Merge observed messages with input messages
        msgs_list: list[Msg] = list(self._observed_msgs)

        if msg is not None:
            if isinstance(msg, Msg):
                msgs_list.append(msg)
            else:
                msgs_list.extend(msg)

        # Filter out None values
        msgs_list = [m for m in msgs_list if m is not None]

        # If no messages to send, return early with a prompt message
        if not msgs_list:
            logger.debug(
                "[%s] No messages to send, returning prompt message",
                self.__class__.__name__,
            )
            response_msg = Msg(
                name=self.name,
                content="No input message provided. How can I help you?",
                role="assistant",
            )
            await self.print(response_msg, True)
            return response_msg

        logger.debug(
            "[%s] Processing %d message(s) for A2A conversion",
            self.__class__.__name__,
            len(msgs_list),
        )

        a2a_message = self._convert_msgs_to_a2a_message(msgs_list)

        response_msg = None

        client : Optional[Client] = None

        try:
            # Create A2A client and send message
            client = self._a2a_client_factory.create(
                card = await self._get_agent_card(),
            )

            logger.debug(
                "[%s] Sending message to remote agent: %s",
                self.__class__.__name__,
                self.name,
            )

            async for item in client.send_message(a2a_message):
                if isinstance(item, A2AMessage):
                    response_msg = self._convert_a2a_message_to_msg(item)
                    await self.print(response_msg, False)
                    logger.debug(
                        "[%s] Received direct message response",
                        self.__class__.__name__,
                    )

                elif isinstance(item, tuple):
                    task, update_event = item

                    # Priority: process Task first if available
                    if task is not None:
                        logger.debug(
                            "[%s] Task update: %s, task_id: %s",
                            self.__class__.__name__,
                            task.status.state.value,
                            task.id,
                        )

                        # Construct message from task status
                        status_msg = self._construct_msg_from_task_status(task)
                        await self.print(status_msg, False)

                        # Convert task artifacts
                        artifact_msg = self._convert_task_artifacts_to_msg(
                            task,
                        )
                        if artifact_msg:
                            await self.print(artifact_msg, False)

                        # Check task status to determine response
                        if task.status.state == TaskState.completed:
                            response_msg = artifact_msg or status_msg
                            logger.debug(
                                "[%s] Task completed successfully: %s",
                                self.__class__.__name__,
                                task.id,
                            )
                        else:
                            response_msg = status_msg
                            logger.debug(
                                "[%s] Task update: %s, id: %s",
                                self.__class__.__name__,
                                task.status.state.value,
                                task.id,
                            )

                    # Fallback: process update_event if no task
                    elif update_event is not None:
                        from a2a.types import (
                            TaskStatusUpdateEvent,
                            TaskArtifactUpdateEvent,
                        )

                        if isinstance(update_event, TaskStatusUpdateEvent):
                            response_msg = self._convert_status_event_to_msg(
                                update_event,
                            )
                            await self.print(response_msg, False)
                            logger.debug(
                                "[%s] Received status update event: %s",
                                self.__class__.__name__,
                                update_event.status.state.value,
                            )

                        elif isinstance(
                            update_event,
                            TaskArtifactUpdateEvent,
                        ):
                            response_msg = self._convert_artifact_event_to_msg(
                                update_event,
                            )
                            await self.print(response_msg, False)
                            logger.debug(
                                "[%s] Received artifact update event: %s",
                                self.__class__.__name__,
                                update_event.artifact.artifact_id,
                            )

        except asyncio.CancelledError:
            # User interruption - re-raise for proper cancellation handling
            logger.debug(
                "[%s] Communication cancelled by user",
                self.__class__.__name__,
            )
            raise

        except Exception as e:
            # Log error and return error message instead of raising
            logger.error(
                "[%s] Failed to get response from remote agent: %s",
                self.__class__.__name__,
                e,
                exc_info=True,
            )

            # Create an error response message
            error_text = (
                f"Error communicating with remote agent: "
                f"{type(e).__name__}: {str(e)}"
            )
            response_msg = Msg(
                name=self.name,
                content=[TextBlock(type="text", text=error_text)],
                role="assistant",
                metadata={"error": True, "error_type": type(e).__name__},
            )

        finally:
            # Ensure we have a response message
            if response_msg is None:
                logger.warning(
                    "[%s] No response received from remote agent",
                    self.__class__.__name__,
                )
                response_msg = Msg(
                    name=self.name,
                    content=[
                        TextBlock(
                            type="text",
                            text="No response received from remote agent",
                        ),
                    ],
                    role="assistant",
                    metadata={"error": True, "error_type": "NoResponse"},
                )

            if isinstance(client, BaseClient):
                await client.close()

        # Clear observed messages after processing
        self._observed_msgs.clear()

        await self.print(response_msg, True)
        return response_msg

    async def handle_interrupt(
        self,
        _msg: Msg | list[Msg] | None = None,
        _structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """The post-processing logic when the reply is interrupted by the
        user or something else.

        Args:
                _msg (`Msg | list[Msg] | None`, optional):
                        The input message(s) to the agent.
                _structured_model (`Type[BaseModel] | None`, optional):
                        The required structured output model.
        """

        response_msg = Msg(
            self.name,
            "I noticed that you have interrupted me. What can I "
            "do for you?",
            "assistant",
            metadata={
                # Expose this field to indicate the interruption
                "_is_interrupted": True,
            },
        )

        await self.print(response_msg, True)

        # Add to observed messages for context in next reply
        self._observed_msgs.append(response_msg)

        return response_msg

    def _convert_task_status_to_msg(
        self,
        status: TaskStatus,
        task_id: str,
    ) -> Msg:
        """Convert a TaskStatus to an AgentScope Msg.

        This is the core method for converting TaskStatus, reused by both
        Task processing and TaskStatusUpdateEvent processing.

        Args:
                status (`TaskStatus`):
                        The TaskStatus object to convert.
                task_id (`str`):
                        The ID of the task.

        Returns:
                `Msg`:
                        Constructed message with task state information.
        """
        if status.message:
            # Convert A2A message to Msg
            response_msg = self._convert_a2a_message_to_msg(status.message)

            # Add task state info at the beginning of content
            status_text = f"[Task ID: {task_id}, Status: {status.state.value}]"
            state_info_block = TextBlock(type="text", text=status_text)

            # Ensure content is a list and prepend state info
            if isinstance(response_msg.content, str):
                response_msg.content = [
                    state_info_block,
                    TextBlock(type="text", text=response_msg.content),
                ]
            elif isinstance(response_msg.content, list):
                response_msg.content.insert(0, state_info_block)
            else:
                response_msg.content = [state_info_block]
        else:
            # No message in status, create new Msg with task state info
            status_text = f"[Task ID: {task_id}, Status: {status.state.value}]"
            state_info_block = TextBlock(type="text", text=status_text)
            response_msg = Msg(
                name=self.name,
                content=[state_info_block],
                role="assistant",
            )

        return response_msg

    def _convert_artifact_to_content_blocks(
        self,
        artifact: Artifact,
    ) -> list[ContentBlock]:
        """Convert an Artifact to a list of ContentBlocks.

        This is the core method for converting Artifact, reused by both
        Task processing and TaskArtifactUpdateEvent processing.

        Args:
                artifact (`Artifact`):
                        The Artifact object to convert.

        Returns:
                `list[ContentBlock]`:
                        List of converted ContentBlocks.
        """
        content_blocks: list[ContentBlock] = []
        for part in artifact.parts:
            block = self._convert_part_to_content_block(part)
            if block:
                content_blocks.append(block)
        return content_blocks

    def _construct_msg_from_task_status(self, task: Task) -> Msg:
        """Construct an AgentScope Msg from an A2A Task status.

        Args:
                task (`Task`):
                        The A2A Task object containing status information.

        Returns:
                `Msg`:
                        Constructed message with task state information.
                        The message will include a TextBlock at the
                        beginning showing the task ID and current status.
        """
        return self._convert_task_status_to_msg(task.status, task.id)

    def _convert_task_artifacts_to_msg(self, task: Task) -> Msg | None:
        """Convert all task artifacts to a single AgentScope Msg.

        Args:
                task (`Task`):
                        The A2A Task object containing artifacts to convert.

        Returns:
                `Msg | None`:
                        A merged message with all artifact parts as
                        ContentBlocks, or `None` if the task has no
                        artifacts. Each artifact's metadata is preserved
                        in the Msg metadata using artifact_id as the key.
        """
        if not task.artifacts or len(task.artifacts) == 0:
            return None

        all_content_blocks: list[ContentBlock] = []
        artifacts_metadata: dict = {}

        # Process all artifacts using the core method
        for artifact in task.artifacts:
            blocks = self._convert_artifact_to_content_blocks(artifact)
            all_content_blocks.extend(blocks)

            # Preserve artifact metadata using artifact_id as key
            if artifact.metadata:
                artifacts_metadata[artifact.artifact_id] = artifact.metadata

        # Build final metadata
        metadata: dict = {}
        if artifacts_metadata:
            metadata["_a2a_artifacts_metadata"] = artifacts_metadata

        # Create message with all content blocks
        return Msg(
            name=self.name,
            content=all_content_blocks if all_content_blocks else "",
            role="assistant",
            metadata=metadata if metadata else None,
        )

    def _convert_status_event_to_msg(
        self,
        event: TaskStatusUpdateEvent,
    ) -> Msg:
        """Convert a TaskStatusUpdateEvent to an AgentScope Msg.

        This method handles TaskStatusUpdateEvent from streaming responses,
        reusing the core _convert_task_status_to_msg method.

        Args:
                event (`TaskStatusUpdateEvent`):
                        The status update event to convert.

        Returns:
                `Msg`:
                        Constructed message with task state information.
        """
        msg = self._convert_task_status_to_msg(event.status, event.task_id)

        # Preserve existing metadata and add event-specific metadata
        if msg.metadata is None:
            msg.metadata = {}

        # Preserve event's original metadata
        if event.metadata:
            msg.metadata.update(event.metadata)

        # Add event-specific fields (these take precedence)
        msg.metadata["_a2a_event_type"] = "status_update"
        msg.metadata["_a2a_is_final"] = event.final
        msg.metadata["_a2a_context_id"] = event.context_id

        return msg

    def _convert_artifact_event_to_msg(
        self,
        event: TaskArtifactUpdateEvent,
    ) -> Msg:
        """Convert a TaskArtifactUpdateEvent to an AgentScope Msg.

        This method handles TaskArtifactUpdateEvent from streaming responses,
        reusing the core _convert_artifact_to_content_blocks method.

        Args:
                event (`TaskArtifactUpdateEvent`):
                        The artifact update event to convert.

        Returns:
                `Msg`:
                        Constructed message with artifact content.
        """
        # Convert artifact to content blocks using core method
        content_blocks = self._convert_artifact_to_content_blocks(
            event.artifact,
        )

        # Build metadata: preserve original metadata from event and artifact
        metadata: dict = {}

        # Preserve artifact's original metadata
        if event.artifact.metadata:
            metadata.update(event.artifact.metadata)

        # Preserve event's original metadata
        if event.metadata:
            metadata.update(event.metadata)

        # Add event-specific fields (these take precedence)
        metadata["_a2a_event_type"] = "artifact_update"
        metadata["_a2a_task_id"] = event.task_id
        metadata["_a2a_context_id"] = event.context_id
        metadata["_a2a_artifact_id"] = event.artifact.artifact_id
        metadata["_a2a_artifact_name"] = event.artifact.name
        metadata["_a2a_is_append"] = event.append
        metadata["_a2a_is_last_chunk"] = event.last_chunk

        return Msg(
            name=self.name,
            content=content_blocks if content_blocks else "",
            role="assistant",
            metadata=metadata,
        )

    def _convert_a2a_message_to_msg(self, a2a_message: A2AMessage) -> Msg:
        """Convert an A2A Message to an AgentScope Msg.

        Args:
                a2a_message (`A2AMessage`):
                        The A2A Message to convert.

        Returns:
                `Msg`:
                        The converted AgentScope message. If the message
                        contains AgentScope metadata, the original Msg is
                        reconstructed. Otherwise, a new Msg with an empty
                        name is created.
        """
        from a2a.types import Role as A2ARole

        # Group parts by msg_id to reconstruct original messages
        msg_groups = OrderedDict()  # Preserve order
        parts_without_metadata = []

        # Process all parts
        for part in a2a_message.parts:
            # Convert part to content block
            content_block = self._convert_part_to_content_block(part)
            if content_block is None:
                continue

            # Check if part has AgentScope metadata
            part_metadata = (
                part.root.metadata if hasattr(part.root, "metadata") else None
            )
            msg_id = (
                part_metadata.get("_agentscope_msg_id")
                if part_metadata
                else None
            )

            if msg_id:
                # Has AgentScope metadata - group by msg_id
                if msg_id not in msg_groups:
                    msg_groups[msg_id] = {
                        "msg_id": msg_id,
                        "msg_source": part_metadata.get(
                            "_agentscope_msg_source",
                            "",
                        ),
                        "content_blocks": [],
                    }
                msg_groups[msg_id]["content_blocks"].append(content_block)
            else:
                # No AgentScope metadata
                parts_without_metadata.append(content_block)

        # Case 1: Has AgentScope metadata - reconstruct Msg(s)
        if msg_groups:
            # Normally single message, but handle multiple just in case
            msgs = []
            for msg_id, group_data in msg_groups.items():
                # Get metadata from A2A message metadata
                msg_metadata = None
                if a2a_message.metadata and msg_id in a2a_message.metadata:
                    msg_metadata = a2a_message.metadata[msg_id]

                # Determine role based on A2A message role
                role: Literal["user", "assistant"] = (
                    "assistant"
                    if a2a_message.role == A2ARole.agent
                    else "user"
                )

                msg = Msg(
                    name=self.name,
                    content=group_data["content_blocks"],
                    role=role,
                    metadata=msg_metadata,
                )
                msgs.append(msg)

            # Return first message (normal case)
            return msgs[0]

        # Case 2: No AgentScope metadata - create new Msg
        elif parts_without_metadata:
            role2: Literal["user", "assistant"] = (
                "assistant" if a2a_message.role == A2ARole.agent else "user"
            )

            msg = Msg(
                name=self.name,
                content=parts_without_metadata,
                role=role2,
                metadata=a2a_message.metadata,
            )
            return msg

        # Case 3: No valid content
        else:
            logger.debug(
                "[%s] No valid content found in A2A message",
                self.__class__.__name__,
            )
            role3: Literal["user", "assistant"] = (
                "assistant" if a2a_message.role == A2ARole.agent else "user"
            )
            return Msg(
                name=self.name,
                content="",
                role=role3,
                metadata=a2a_message.metadata,
            )

    def _convert_part_to_content_block(
        self,
        part: Part,
    ) -> ContentBlock | None:
        """Convert A2A Part to AgentScope ContentBlock.

        Handles three types of parts:
        - TextPart -> TextBlock or ThinkingBlock (based on metadata)
        - FilePart -> ImageBlock/AudioBlock/VideoBlock (based on mime type)
        - DataPart -> ToolUseBlock/ToolResultBlock (with AgentScope metadata)
          or TextBlock (without metadata)

        Args:
                part (`Part`):
                        The A2A Part to convert.

        Returns:
                `ContentBlock | None`:
                        The converted ContentBlock, or `None` if the part
                        type is unknown or conversion fails.
        """
        from a2a.types import TextPart, FilePart, DataPart

        part_root = part.root

        # Case 1: TextPart
        if isinstance(part_root, TextPart):
            text_content = part_root.text

            # Check for AgentScope metadata
            part_metadata = (
                part_root.metadata if hasattr(part_root, "metadata") else None
            )
            block_type = (
                part_metadata.get("_agentscope_block_type")
                if part_metadata
                else None
            )

            if block_type == "thinking":
                # Convert to ThinkingBlock
                return ThinkingBlock(type="thinking", thinking=text_content)
            else:
                # Convert to TextBlock (default)
                return TextBlock(type="text", text=text_content)

        # Case 2: FilePart
        elif isinstance(part_root, FilePart):
            return self._convert_file_part_to_media_block(part_root)

        # Case 3: DataPart
        elif isinstance(part_root, DataPart):
            return self._convert_data_part_to_block(part_root)

        # Unknown part type
        else:
            logger.debug(
                "[%s] Unknown part type: %s",
                self.__class__.__name__,
                type(part_root),
            )
            return None

    def _convert_data_part_to_block(self, data_part: DataPart) -> ContentBlock:
        """Convert an A2A DataPart to an AgentScope ContentBlock.

        Args:
                data_part (`DataPart`):
                        The A2A DataPart to convert.

        Returns:
                `ContentBlock`:
                        The converted ToolUseBlock, ToolResultBlock
                        (if AgentScope metadata present), or TextBlock
                        (JSON-serialized).
        """
        # Check for AgentScope metadata
        part_metadata = (
            data_part.metadata if hasattr(data_part, "metadata") else None
        )
        block_type = (
            part_metadata.get("_agentscope_block_type")
            if part_metadata
            else None
        )

        # Case 1: ToolUse block
        if block_type == "tool_use":
            tool_name = part_metadata.get("_agentscope_tool_name")
            tool_id = part_metadata.get("_agentscope_tool_call_id")
            tool_input = data_part.data  # Data.data → ToolUse.input

            return ToolUseBlock(
                type="tool_use",
                id=tool_id,
                name=tool_name,
                input=tool_input,
            )

        # Case 2: ToolResult block
        elif block_type == "tool_result":
            tool_name = part_metadata.get("_agentscope_tool_name")
            tool_id = part_metadata.get("_agentscope_tool_call_id")
            tool_output = data_part.data.get(
                "_agentscope_tool_output",
            )  # Data.data._agentscope_tool_output → ToolResult.output

            return ToolResultBlock(
                type="tool_result",
                id=tool_id,
                name=tool_name,
                output=tool_output,
            )

        # Case 3: No AgentScope metadata - serialize to JSON TextBlock
        else:
            data_json = json.dumps(
                data_part.data,
                ensure_ascii=False,
                indent=2,
            )
            return TextBlock(type="text", text=data_json)

    def _convert_file_part_to_media_block(
        self,
        file_part: FilePart,
    ) -> ImageBlock | AudioBlock | VideoBlock | None:
        """Convert an A2A FilePart to a media ContentBlock.

        Args:
                file_part (`FilePart`):
                        The A2A FilePart to convert.

        Returns:
                `ImageBlock | AudioBlock | VideoBlock | None`:
                        The converted media block, or `None` if the media type
                        cannot be determined from the MIME type.
        """
        from a2a.types import FileWithUri, FileWithBytes

        file_obj = file_part.file

        # Determine media type from mime_type
        mime_type = getattr(file_obj, "mime_type", None)
        block_type = None

        if mime_type:
            if mime_type.startswith("image/"):
                block_type = "image"
            elif mime_type.startswith("audio/"):
                block_type = "audio"
            elif mime_type.startswith("video/"):
                block_type = "video"

        if not block_type or block_type not in ["image", "audio", "video"]:
            logger.warning(
                "[%s] Unable to determine media type for FilePart "
                "(mime_type: %s)",
                self.__class__.__name__,
                mime_type,
            )
            return None

        # Convert FileWithUri or FileWithBytes to source
        source = None
        if isinstance(file_obj, FileWithUri):
            # URL source
            source = URLSource(type="url", url=file_obj.uri)
        elif isinstance(file_obj, FileWithBytes):
            # Base64 source
            source = Base64Source(
                type="base64",
                media_type=file_obj.mime_type or "application/octet-stream",
                data=file_obj.bytes,
            )

        if not source:
            logger.warning(
                "[%s] Unable to convert file object to source",
                self.__class__.__name__,
            )
            return None

        # Create appropriate media block
        if block_type == "image":
            return ImageBlock(type="image", source=source)
        elif block_type == "audio":
            return AudioBlock(type="audio", source=source)
        elif block_type == "video":
            return VideoBlock(type="video", source=source)

        return None

    def _convert_msgs_to_a2a_message(self, msgs: list[Msg]) -> A2AMessage:
        """Convert a list of AgentScope Msgs to a single A2A Message.

        Args:
                msgs (`list[Msg]`):
                        List of AgentScope messages to convert and merge.

        Returns:
                `A2AMessage`:
                        A single A2A Message containing all content from
                        the input messages, with tracking metadata preserved.
        """
        from a2a.types import (
            Message as A2AMessage,
            Part,
            Role as A2ARole,
            TextPart,
        )

        merged_parts = []
        a2a_metadata = {}

        # Process all messages
        for msg in msgs:
            if msg is None:
                continue

            # Store msg metadata in A2A message metadata using msg.id as key
            if msg.metadata and len(msg.metadata) > 0:
                a2a_metadata[msg.id] = msg.metadata

            # Prepare part metadata with source information
            part_metadata = {
                "_agentscope_msg_source": msg.name,
                "_agentscope_msg_id": msg.id,
            }

            # Process message content
            if isinstance(msg.content, str):
                # Simple string content - convert to text part with metadata
                if msg.content.strip():  # Filter empty strings
                    merged_parts.append(
                        Part(
                            root=TextPart(
                                text=msg.content,
                                metadata=part_metadata,
                            ),
                        ),
                    )

            elif isinstance(msg.content, list):
                # ContentBlock list - process all types
                for block in msg.content:
                    part = self._convert_content_block_to_part(block)
                    if part:  # Filter None results
                        # Merge metadata on the part's root object
                        if part.root.metadata:
                            part.root.metadata.update(part_metadata)
                        else:
                            part.root.metadata = part_metadata
                        merged_parts.append(part)

        # If no parts were extracted, add empty text part
        if not merged_parts:
            merged_parts.append(Part(root=TextPart(text="", metadata=None)))
            logger.debug(
                "[%s] No valid content in messages, using empty text part",
                self.__class__.__name__,
            )

        # Build A2A Message with merged content
        a2a_message = A2AMessage(
            message_id=str(uuid4()),
            role=A2ARole.user,
            parts=merged_parts,
            metadata=a2a_metadata if a2a_metadata else None,
        )

        logger.debug(
            "[%s] Merged %d message(s) into A2A message with %d part(s)",
            self.__class__.__name__,
            len(msgs),
            len(merged_parts),
        )

        return a2a_message

    def _convert_content_block_to_part(
        self,
        block: ContentBlock,
    ) -> Part | None:
        """Convert an AgentScope ContentBlock to an A2A Part.

        Args:
                block (`ContentBlock`):
                        The AgentScope ContentBlock to convert.

        Returns:
                `Part | None`:
                        The converted A2A Part object, or `None` if the
                        block is empty or of an unsupported type.
        """
        from a2a.types import Part, TextPart, DataPart

        block_type = block["type"]

        # Text and Thinking blocks -> TextPart
        if block_type == "text":
            text = block.get("text", "")
            if text and text.strip():
                return Part(
                    root=TextPart(
                        text=text,
                        metadata={"_agentscope_block_type": "text"},
                    ),
                )
            return None

        elif block_type == "thinking":
            thinking = block.get("thinking", "")
            if thinking and thinking.strip():
                return Part(
                    root=TextPart(
                        text=thinking,
                        metadata={"_agentscope_block_type": "thinking"},
                    ),
                )
            return None

        # Image, Audio, Video blocks -> FilePart
        elif block_type in ["image", "audio", "video"]:
            return self._convert_media_block_to_file_part(block, block_type)

        # ToolUse block -> DataPart
        elif block_type == "tool_use":
            tool_id = block.get("id")
            tool_name = block.get("name")
            tool_input = block.get("input", {})

            return Part(
                root=DataPart(
                    data=tool_input,  # ToolUse.input → Data.data
                    metadata={
                        "_agentscope_block_type": "tool_use",
                        "_agentscope_tool_name": tool_name,
                        "_agentscope_tool_call_id": tool_id,
                    },
                ),
            )

        # ToolResult block -> DataPart
        elif block_type == "tool_result":
            tool_id = block.get("id")
            tool_name = block.get("name")
            tool_output = block.get("output")

            return Part(
                root=DataPart(
                    data={
                        "_agentscope_tool_output": tool_output,
                    },  # ToolResult.output → Data.data._agentscope_tool_output
                    metadata={
                        "_agentscope_block_type": "tool_result",
                        "_agentscope_tool_name": tool_name,
                        "_agentscope_tool_call_id": tool_id,
                    },
                ),
            )

        # Unknown or unsupported block type
        else:
            logger.debug(
                "[%s] Unsupported content block type: %s",
                self.__class__.__name__,
                block_type,
            )
            return None

    def _convert_media_block_to_file_part(
        self,
        block: ContentBlock,
        media_type: str,
    ) -> Part | None:
        """Convert a media ContentBlock to an A2A FilePart.

        Args:
                block (`ContentBlock`):
                        The media block (ImageBlock, AudioBlock, VideoBlock).
                media_type (`str`):
                        The type of media: "image", "audio", or "video".

        Returns:
                `Part | None`:
                        An A2A Part containing a FilePart, or `None` if the
                        conversion fails.
        """
        from a2a.types import Part, FilePart, FileWithUri, FileWithBytes

        source = block.get("source")
        if not source:
            logger.warning(
                "[%s] %s block missing source",
                self.__class__.__name__,
                media_type,
            )
            return None

        source_type = source.get("type")

        # URL source -> FileWithUri
        if source_type == "url":
            url = source.get("url")
            if not url:
                logger.warning(
                    "[%s] %s block with url source missing url",
                    self.__class__.__name__,
                    media_type,
                )
                return None

            # Infer mime_type from media_type
            mime_type = self._infer_mime_type(media_type)

            return Part(
                root=FilePart(
                    file=FileWithUri(uri=url, mime_type=mime_type),
                ),
            )

        # Base64 source -> FileWithBytes
        elif source_type == "base64":
            data = source.get("data")
            mime_type = source.get("media_type")

            if not data:
                logger.warning(
                    "[%s] %s block with base64 source missing data",
                    self.__class__.__name__,
                    media_type,
                )
                return None

            # If no mime_type in source, infer from media_type
            if not mime_type:
                mime_type = self._infer_mime_type(media_type)

            return Part(
                root=FilePart(
                    file=FileWithBytes(
                        bytes=data,
                        mime_type=mime_type,
                    ),
                ),
            )

        else:
            logger.warning(
                "[%s] Unsupported source type '%s' for %s block",
                self.__class__.__name__,
                source_type,
                media_type,
            )
            return None

    def _infer_mime_type(self, media_type: str) -> str:
        """Infer a generic MIME type from a media type category.

        Args:
                media_type (`str`):
                        The media type category: "image", "audio", or "video".

        Returns:
                `str`:
                        The inferred MIME type (e.g., "image/*", "audio/*",
                        "video/*"), or "application/octet-stream" for
                        unknown types.
        """
        # Return generic MIME type based on media category
        if media_type == "image":
            return "image/*"
        elif media_type == "audio":
            return "audio/*"
        elif media_type == "video":
            return "video/*"
        else:
            return "application/octet-stream"

    async def _ensure_ready(self) -> None:
        """Ensure the A2A client is initialized and ready for communication.

        Note:
                This method is idempotent and can be called multiple times
                safely.
        """
        from a2a.client import ClientFactory

        if self._is_ready and self._a2a_client_factory is not None:
            return

        agent_card = await self._agent_card_resolver.get_agent_card()
        await self._validate_agent_card(agent_card)
        self._agent_card = agent_card

        a2a_client_config = self._extract_a2a_client_config()

        self._a2a_client_factory = ClientFactory(
            config=a2a_client_config,
            consumers=self._agent_config.consumers,
        )

        for (
            transport_label,
            transport_producer,
        ) in self._agent_config.additional_transport_producers.items():
            self._a2a_client_factory.register(
                transport_label,
                transport_producer,
            )

        self._is_ready = True

    def _extract_a2a_client_config(self) -> ClientConfig:
        """Extract A2A client configuration from agent config.

        Returns:
                `ClientConfig`:
                        The extracted client configuration
                                                for A2A communication.
        """
        from a2a.client import ClientConfig
        from a2a.types import TransportProtocol

        a2a_client_config = ClientConfig(
            streaming=self._agent_config.streaming,
            polling=self._agent_config.polling,
            httpx_client=self._agent_config.httpx_client
            or httpx.AsyncClient(
                timeout=httpx.Timeout(timeout=600),
            ),
            grpc_channel_factory=self._agent_config.grpc_channel_factory,
            supported_transports=self._agent_config.supported_transports
            or [TransportProtocol.jsonrpc],
            use_client_preference=self._agent_config.use_client_preference,
            accepted_output_modes=self._agent_config.accepted_output_modes,
            push_notification_configs=(
                self._agent_config.push_notification_configs
            ),
        )

        return a2a_client_config

    async def _validate_agent_card(self, agent_card: AgentCard) -> None:
        """Validate the resolved agent card.

        Args:
                agent_card (`AgentCard`):
                        The agent card to validate.

        Raises:
                `RuntimeError`:
                        If the agent card is missing the required URL field.
        """
        if not agent_card.url:
            logger.error(
                "[%s] Agent card missing URL",
                self.__class__.__name__,
            )
            raise RuntimeError(
                "Agent card must have a valid URL for RPC communication",
            )

        # Validate URL format
        try:
            parsed_url = urlparse(str(agent_card.url))
            if not parsed_url.scheme or not parsed_url.netloc:
                logger.error(
                    "[%s] Invalid RPC URL format: %s",
                    self.__class__.__name__,
                    agent_card.url,
                )
                raise ValueError("Invalid RPC URL format")
        except Exception as e:
            logger.error(
                "[%s] Invalid RPC URL in agent card: %s, error: %s",
                self.__class__.__name__,
                agent_card.url,
                e,
            )
            raise RuntimeError(
                f"Invalid RPC URL in agent card: "
                f"{agent_card.url}, error: {e}",
            ) from e
