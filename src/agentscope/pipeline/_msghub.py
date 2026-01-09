# -*- coding: utf-8 -*-
"""MsgHub is designed to share messages among a group of agents."""

from collections.abc import Sequence
from typing import Any

import shortuuid

from .._logging import logger
from ..agent import AgentBase
from ..message import Msg


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
                agent.reset_subscribers(self.name, self.participants)

    def add(
        self,
        new_participant: list[AgentBase] | AgentBase,
    ) -> None:
        """Add new participant into this hub"""
        if isinstance(new_participant, AgentBase):
            new_participant = [new_participant]

        for agent in new_participant:
            if agent not in self.participants:
                self.participants.append(agent)

        self._reset_subscriber()

    def delete(
        self,
        participant: list[AgentBase] | AgentBase,
    ) -> None:
        """Delete agents from participant."""
        if isinstance(participant, AgentBase):
            participant = [participant]

        for agent in participant:
            if agent in self.participants:
                # remove agent from self.participant
                self.participants.pop(self.participants.index(agent))
            else:
                logger.warning(
                    "Cannot find the agent with ID %s, skip its deletion.",
                    agent.id,
                )

        # Remove this agent from the subscriber of other agents
        self._reset_subscriber()

    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        """Broadcast the message to all participants.

        Args:
            msg (`list[Msg] | Msg`):
                Message(s) to be broadcast among all participants.
        """
        for agent in self.participants:
            await agent.observe(msg)

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
