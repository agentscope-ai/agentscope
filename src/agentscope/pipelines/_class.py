# -*- coding: utf-8 -*-
"""Pipeline classes."""

from typing import Callable, Union
from typing import Optional

from ._functional import sequential_pipeline
from ..agents._agent_base import AgentBase
from ..message import Msg


class SequentialPipeline:
    """A sequential pipeline class, which executes a sequence of operators
    sequentially. Compared with functional pipeline, this class can be
    re-used."""

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
