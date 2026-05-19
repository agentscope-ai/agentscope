# -*- coding: utf-8 -*-
"""MsgHub is designed to share messages among a group of agents."""

from collections.abc import Sequence
from typing import Any, List, Optional, Dict

import shortuuid

from .._logging import logger
from ..agent import AgentBase
from ..message import Msg, TopicFilter


class MsgHub:
    """MsgHub class that controls the subscription of the participated agents.

    Example:
        In the following example, the reply message from `agent1`, `agent2`,
        and `agent3` will be broadcast to all the other agents in the MsgHub.

        .. code-block:: python

            with MsgHub(participant=[agent1, agent2, agent3]):
                agent1()
                agent2()

        Actually, it has the same effect as the following code, but much more
        easy and elegant!

        .. code-block:: python

            x1 = agent1()
            agent2.observe(x1)
            agent3.observe(x1)

            x2 = agent2()
            agent1.observe(x2)
            agent3.observe(x2)
    """

    def __init__(
        self,
        participants: Sequence[AgentBase],
        announcement: list[Msg] | Msg | None = None,
        enable_auto_broadcast: bool = True,
        name: str | None = None,
    ) -> None:
        """Initialize a MsgHub context manager.

        Args:
            participants (`Sequence[AgentBase]`):
                A sequence of agents that participate in the MsgHub.
            announcement (`list[Msg] | Msg | None`):
                The message that will be broadcast to all participants when
                entering the MsgHub.
            enable_auto_broadcast (`bool`, defaults to `True`):
                Whether to enable automatic broadcasting of the replied
                message from any participant to all other participants. If
                disabled, the MsgHub will only serve as a manual message
                broadcaster with the `announcement` argument and the
                `broadcast()` method.
            name (`str | None`):
                The name of this MsgHub. If not provided, a random ID
                will be generated.
        """

        self.name = name or shortuuid.uuid()
        self.participants = list(participants)
        self.announcement = announcement
        self.enable_auto_broadcast = enable_auto_broadcast

        self._participant_topics: Dict[str, Optional[List[str]]] = {}
        for agent in self.participants:
            self._participant_topics[agent.id] = None

    async def __aenter__(self) -> "MsgHub":
        """Will be called when entering the MsgHub."""
        self._reset_subscriber()

        # broadcast the input message to all participants
        if self.announcement is not None:
            await self.broadcast(msg=self.announcement)

        return self

    async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
        """Will be called when exiting the MsgHub."""
        if self.enable_auto_broadcast:
            for agent in self.participants:
                agent.remove_subscribers(self.name)

    def _reset_subscriber(self) -> None:
        """Reset the subscriber for agent in `self.participant`"""
        if self.enable_auto_broadcast:
            for agent in self.participants:
                agent.reset_subscribers(
                    self.name,
                    self.participants,
                    self._participant_topics,
                )

    def add(
        self,
        new_participant: list[AgentBase] | AgentBase,
        topics: Optional[List[str]] = None,
    ) -> None:
        """Add new participant into this hub.

        Args:
            new_participant (`list[AgentBase] | AgentBase`):
                The new participant(s) to add.
            topics (`Optional[List[str]]`, optional):
                The topics that the new participant is interested in.
                None or empty list means the participant receives all messages.
        """
        if isinstance(new_participant, AgentBase):
            new_participant = [new_participant]

        for agent in new_participant:
            if agent not in self.participants:
                self.participants.append(agent)
                self._participant_topics[agent.id] = topics

        self._reset_subscriber()

    def add_participant(
        self,
        participant: AgentBase,
        topics: Optional[List[str]] = None,
    ) -> None:
        """Add a single participant with optional topic subscription.

        This is an alias for `add()` with clearer semantics for topic-based
        message routing.

        Args:
            participant (`AgentBase`):
                The agent to add to the MsgHub.
            topics (`Optional[List[str]]`, optional):
                The topics that this participant is interested in. Only messages
                with matching topics will be delivered to this participant.
                - None or empty list: receives all messages (backward compatible)
                - List of patterns: e.g., ["task.*", "notify"] receives messages
                  with topics matching any of these patterns (supports fnmatch
                  wildcards like * and ?)
        """
        self.add(participant, topics)

    def delete(
        self,
        participant: list[AgentBase] | AgentBase,
    ) -> None:
        """Delete agents from participant."""
        if isinstance(participant, AgentBase):
            participant = [participant]

        for agent in participant:
            if agent in self.participants:
                self.participants.pop(self.participants.index(agent))
                if agent.id in self._participant_topics:
                    self._participant_topics.pop(agent.id)
            else:
                logger.warning(
                    "Cannot find the agent with ID %s, skip its deletion.",
                    agent.id,
                )

        self._reset_subscriber()

    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        """Broadcast the message to all participants.

        Messages are filtered based on topics:
        - If participant has no topics, receives all messages
        - If message has no topics, received by all participants
        - Otherwise, check if any message topic matches any participant topic pattern

        Args:
            msg (`list[Msg] | Msg`):
                Message(s) to be broadcast among all participants.
        """
        for agent in self.participants:
            if self._should_deliver_to_participant(msg, agent):
                await agent.observe(msg)

    def _should_deliver_to_participant(
        self,
        msg: list[Msg] | Msg,
        participant: AgentBase,
    ) -> bool:
        """Check if the message should be delivered to a participant.

        Args:
            msg: The message(s) to check.
            participant: The participant to check.

        Returns:
            True if the message should be delivered.
        """
        participant_topics = self._participant_topics.get(participant.id, None)

        if participant_topics is None or len(participant_topics) == 0:
            return True

        if isinstance(msg, list):
            for single_msg in msg:
                if TopicFilter.matches(single_msg.topics, participant_topics):
                    return True
            return False

        return TopicFilter.matches(msg.topics, participant_topics)

    def set_auto_broadcast(self, enable: bool) -> None:
        """Enable automatic broadcasting of the replied message from any
        participant to all other participants.

        Args:
            enable (`bool`):
                Whether to enable automatic broadcasting. If disabled, the
                MsgHub will only serve as a manual message broadcaster with
                the `announcement` argument and the `broadcast()` method.
        """
        if enable:
            self.enable_auto_broadcast = True
            self._reset_subscriber()
        else:
            self.enable_auto_broadcast = False
            for agent in self.participants:
                agent.remove_subscribers(self.name)
