# -*- coding: utf-8 -*-
"""MsgHub is designed to share messages among a group of agent.
"""
from __future__ import annotations
from typing import Any, Optional, Union, Sequence

from loguru import logger

from .agent import AgentBase
from .message import Msg


class MsgHubManager:
    """MsgHub manager class for sharing dialog among a group of agent."""

    def __init__(
        self,
        participants: Sequence[AgentBase],
        announcement: Optional[Union[Sequence[Msg], Msg]] = None,
    ) -> None:
        """Initialize a msghub manager from the given arguments.

        Args:
            participants (`Sequence[AgentBase]`):
                The Sequence of participants in the msghub.
            announcement
                (`Optional[Union[list[Msg], Msg]]`, defaults to `None`):
                The message that will be broadcast to all participants at
                the first without requiring response.
        """
        self.participants = participants
        self.announcement = announcement

    def __enter__(self) -> MsgHubManager:
        """Will be called when entering the msghub."""
        name_participants = [agent.name for agent in self.participants]
        logger.debug(
            "Enter msghub with participants: {}",
            ", ".join(
                name_participants,
            ),
        )

        self._reset_audience()

        # broadcast the input message to all participants
        if self.announcement is not None:
            for agent in self.participants:
                agent.observe(self.announcement)

        return self

    def __exit__(self, *args: Any, **kwargs: Any) -> None:
        """Will be called when exiting the msghub."""
        for agent in self.participants:
            agent.clear_audience()

    def _reset_audience(self) -> None:
        """Reset the audience for agent in `self.participant`"""
        for agent in self.participants:
            agent.reset_audience(self.participants)

    def add(
        self,
        new_participant: Union[Sequence[AgentBase], AgentBase],
    ) -> None:
        """Add new participant into this hub"""
        if isinstance(new_participant, AgentBase):
            new_participant = [new_participant]

        for agent in new_participant:
            if agent not in self.participants:
                self.participants.append(agent)
            else:
                logger.warning(
                    f"Skip adding agent [{agent.name}] for it has "
                    "already joined in.",
                )

        self._reset_audience()

    def delete(
        self,
        participant: Union[Sequence[AgentBase], AgentBase],
    ) -> None:
        """Delete agent from participant."""
        if isinstance(participant, AgentBase):
            participant = [participant]

        for agent in participant:
            if agent in self.participants:
                # Clear the audience of the deleted agent firstly
                agent.clear_audience()

                # remove agent from self.participant
                self.participants.pop(self.participants.index(agent))
            else:
                logger.warning(
                    f"Cannot find agent [{agent.name}], skip its"
                    f" deletion.",
                )

        # Remove this agent from the audience of other agent
        self._reset_audience()

    def broadcast(self, msg: Union[Msg, Sequence[Msg]]) -> None:
        """Broadcast the message to all participants.

        Args:
            msg (`Union[Msg, Sequence[Msg]]`):
                One or a list of dict messages to broadcast among all
                participants.
        """
        for agent in self.participants:
            agent.observe(msg)


def msghub(
    participants: Sequence[AgentBase],
    announcement: Optional[Union[Sequence[Msg], Msg]] = None,
) -> MsgHubManager:
    """msghub is used to share messages among a group of agent.

    Args:
        participants (`Sequence[AgentBase]`):
            A Sequence of participated agent in the msghub.
        announcement (`Optional[Union[list[Msg], Msg]]`, defaults to `None`):
            The message that will be broadcast to all participants at the
            very beginning without requiring response.

    Example:
        In the following code, we create a msghub with three agent, and each
        message output by `agent1`, `agent2`, `agent3` will be passed to all
        other agent, that's what we mean msghub.

        .. code-block:: python

            with msghub(participant=[agent1, agent2, agent3]):
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
    return MsgHubManager(participants, announcement)
