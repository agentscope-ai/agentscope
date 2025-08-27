# -*- coding: utf-8 -*-
"""Pipeline classes."""
from typing import Any

from ._functional import sequential_pipeline, fanout_pipeline
from ..agent import AgentBase
from ..message import Msg


class SequentialPipeline:
    """An async sequential pipeline class, which executes a sequence of
    agents sequentially. Compared with functional pipeline, this class
    can be re-used."""

    def __init__(
        self,
        agents: list[AgentBase],
    ) -> None:
        """Initialize a sequential pipeline class

        Args:
            agents (`list[AgentBase]`):
                A list of agents.
        """
        self.agents = agents

    async def __call__(
        self,
        msg: Msg | list[Msg] | None = None,
    ) -> Msg | list[Msg] | None:
        """Execute the sequential pipeline

        Args:
            msg (`Msg | list[Msg] | None`, defaults to `None`):
                The initial input that will be passed to the first agent.
        """
        return await sequential_pipeline(
            agents=self.agents,
            msg=msg,
        )


class FanoutPipeline:
    """An async fanout pipeline class, which distributes the same input to
    multiple agents. Compared with functional pipeline, this class can be
    re-used and configured with default parameters."""

    def __init__(
        self,
        agents: list[AgentBase],
        enable_gather: bool = True,
    ) -> None:
        """Initialize a fanout pipeline class

        Args:
            agents (`list[AgentBase]`):
                A list of agents to execute.
            enable_gather (`bool`, defaults to `True`):
                Whether to execute agents concurrently
                using `asyncio.gather()`. If False, agents are executed
                sequentially.
        """
        self.agents = agents
        self.enable_gather = enable_gather

    async def __call__(
        self,
        msg: Msg | list[Msg] | None = None,
        **kwargs: Any,
    ) -> list[Msg]:
        """Execute the fanout pipeline

        Args:
            msg (`Msg | list[Msg] | None`, defaults to `None`):
                The input message that will be distributed to all agents.
            **kwargs (`Any`):
                Additional keyword arguments passed to each agent during
                execution.

        Returns:
            `list[Msg]`:
                A list of output messages from all agents.
        """

        return await fanout_pipeline(
            agents=self.agents,
            msg=msg,
            enable_gather=self.enable_gather,
            **kwargs,
        )
