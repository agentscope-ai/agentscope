# -*- coding: utf-8 -*-
"""VoiceMsgHub for managing voice conversation participants, similar to
MsgHub."""

from typing import (
    TYPE_CHECKING,
    Any,
    Sequence,
    List,
    Protocol,
    runtime_checkable,
    Optional,
    Type,
)

from agentscope._logging import logger

from ._msg_stream import MsgStream

# For type hints only, avoiding circular imports
if TYPE_CHECKING:
    from ..agent._voice_agent import VoiceAgent
    from ._voice_user_input import RealtimeVoiceInput


@runtime_checkable
class MsgStreamParticipant(Protocol):
    """Protocol for participants that support MsgStream."""

    def set_msg_stream(self, msg_stream: MsgStream) -> None:
        """Set the message stream.

        Args:
            msg_stream (`MsgStream`):
                The message stream instance to use for communication.
        """


class VoiceMsgHub:
    """VoiceMsgHub manages participants in voice conversations.

    Similar to MsgHub, but designed for voice messages. Supports VoiceAgent
    and RealtimeVoiceInput as participants, providing a centralized message
    stream for all participants to communicate.

    The hub acts as a context manager, automatically initializing participants
    on entry and cleaning up resources on exit.

    Examples:
        Two agents conversation:

        .. code-block:: python

            agent1 = VoiceAgent(name="Alice", model=model1, sys_prompt="...")
            agent2 = VoiceAgent(name="Bob", model=model2, sys_prompt="...")

            async with VoiceMsgHub(participants=[agent1, agent2]) as hub:
                await agent1.say("Hello")
                await agent2.reply()

        User and agent conversation:

        .. code-block:: python

            voice_input = RealtimeVoiceInput()  # No need to pass msg_stream
            agent = VoiceAgent(name="assistant", model=model, sys_prompt="...")

            async with VoiceMsgHub(participants=[voice_input, agent]) as hub:
                await voice_input.start()
                await agent.reply()
    """

    def __init__(
        self,
        participants: Sequence[MsgStreamParticipant],
        name: Optional[str] = None,
    ) -> None:
        """Initialize the VoiceMsgHub.

        Args:
            participants (`Sequence[MsgStreamParticipant]`):
                List of conversation participants. Must be instances that
                implement the MsgStreamParticipant protocol (VoiceAgent or
                RealtimeVoiceInput).
            name (`Optional[str]`, defaults to `None`):
                Name of the hub for identification. If None, defaults to
                "voice_hub".

        Raises:
            `TypeError`:
                If any participant doesn't match expected types (VoiceAgent or
                RealtimeVoiceInput).
        """
        self.name = name or "voice_hub"
        self._agents: List["VoiceAgent"] = []
        self._voice_inputs: List["RealtimeVoiceInput"] = []

        # Use duck typing to categorize participants, avoiding circular imports
        for p in participants:
            # VoiceAgent has 'reply' method and '_model' attribute
            if hasattr(p, "reply") and hasattr(p, "_model"):
                self._agents.append(p)  # type: ignore
            # RealtimeVoiceInput has 'start' method but no '_model' attribute
            elif hasattr(p, "start") and not hasattr(p, "_model"):
                self._voice_inputs.append(p)  # type: ignore
            else:
                raise TypeError(f"Unsupported participant type: {type(p)}")

        self._msg_stream = MsgStream()
        self._initialized = False

    async def __aenter__(self) -> "VoiceMsgHub":
        """Enter context and initialize all participants.

        Sets up the message stream for all participants and initializes
        all agents. This method is called when entering the async context.

        Returns:
            `VoiceMsgHub`:
                Self reference for context manager usage.
        """
        # Set msg_stream for all participants
        for agent in self._agents:
            agent.set_msg_stream(self._msg_stream)
        for voice_input in self._voice_inputs:
            voice_input.set_msg_stream(self._msg_stream)

        # Initialize all agents
        for agent in self._agents:
            await agent.initialize()

        self._initialized = True
        logger.info(
            "VoiceMsgHub '%s' started with %d agents, %d voice inputs",
            self.name,
            len(self._agents),
            len(self._voice_inputs),
        )
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        """Exit context and clean up resources.

        Stops all voice inputs, closes all agents, and closes the message
        stream. This method is called when exiting the async context.

        Args:
            exc_type (`Optional[Type[BaseException]]`):
                Exception type if an exception occurred.
            exc_val (`Optional[BaseException]`):
                Exception instance if an exception occurred.
            exc_tb (`Optional[Any]`):
                Traceback object if an exception occurred.
        """
        # Stop all voice inputs
        for voice_input in self._voice_inputs:
            await voice_input.stop()

        # Stop and close all agents
        for agent in self._agents:
            agent.stop()
        for agent in self._agents:
            await agent.close()

        # Close MsgStream
        await self._msg_stream.close()

        self._initialized = False
        logger.info("VoiceMsgHub '%s' closed", self.name)

    @property
    def agents(self) -> List["VoiceAgent"]:
        """Get all agents in the hub.

        Returns:
            `List[VoiceAgent]`:
                List of all VoiceAgent instances managed by this hub.
        """
        return self._agents

    @property
    def voice_inputs(self) -> List["RealtimeVoiceInput"]:
        """Get all voice inputs in the hub.

        Returns:
            `List[RealtimeVoiceInput]`:
                List of all RealtimeVoiceInput instances managed by this hub.
        """
        return self._voice_inputs

    @property
    def msg_stream(self) -> MsgStream:
        """Get the internal message stream.

        Returns:
            `MsgStream`:
                The message stream instance used for inter-participant
                communication.
        """
        return self._msg_stream

    @property
    def is_initialized(self) -> bool:
        """Check if the hub is initialized.

        Returns:
            `bool`:
                True if the hub has been initialized (entered context),
                False otherwise.
        """
        return self._initialized

    @property
    def participant_count(self) -> int:
        """Get the total number of participants.

        Returns:
            `int`:
                Total count of agents and voice inputs in this hub.
        """
        return len(self._agents) + len(self._voice_inputs)
