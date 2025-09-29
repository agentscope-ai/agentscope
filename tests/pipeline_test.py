# -*- coding: utf-8 -*-
# pylint: disable=signature-differs
"""Unit tests for pipeline classes and functions"""

import unittest
from typing import Any

from agentscope.message import Msg
from agentscope.pipelines import (
    SequentialPipeline,
    sequential_pipeline,
)

from agentscope.agents import AgentBase


class AddAgent(AgentBase):
    """Add agent class."""

    def __init__(self, value: int) -> None:
        """Initialize the agent"""
        super().__init__()
        self.name = "Add"
        self.value = value

    async def reply(self, x: Msg | None) -> Msg | None:
        """Reply function"""
        if x is None:
            return None
        x.metadata["result"] += self.value
        return x

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Observe function"""

    async def handle_interrupt(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """Handle interrupt"""


class MultAgent(AgentBase):
    """Mult agent class."""

    def __init__(self, value: int) -> None:
        """Initialize the agent"""
        super().__init__()
        self.name = "Mult"
        self.value = value

    async def reply(self, x: Msg | None) -> Msg | None:
        """Reply function"""
        if x is None:
            return None
        x.metadata["result"] *= self.value
        return x

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Observe function"""

    async def handle_interrupt(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """Handle interrupt"""


class PipelineTest(unittest.TestCase):
    """Test cases for Pipelines"""

    async def test_functional_sequential_pipeline(self) -> None:
        """Test SequentialPipeline executes agents sequentially"""

        add1 = AddAgent(1)
        add2 = AddAgent(2)
        mult3 = MultAgent(3)

        x = Msg("user", "", "user", metadata={"result": 0})
        res = sequential_pipeline([add1, add2, mult3], x)
        self.assertEqual(9, res.metadata["result"])

        x = Msg("user", "", "user", metadata={"result": 0})
        res = sequential_pipeline([add1, mult3, add2], x)
        self.assertEqual(5, res.metadata["result"])

        x = Msg("user", "", "user", metadata={"result": 0})
        res = sequential_pipeline([mult3, add1, add2], x)
        self.assertEqual(3, res.metadata["result"])

    async def test_class_sequential_pipeline(self) -> None:
        """Test SequentialPipeline executes agents sequentially"""

        add1 = AddAgent(1)
        add2 = AddAgent(2)
        mult3 = MultAgent(3)

        x = Msg("user", "", "user", metadata={"result": 0})
        pipeline = SequentialPipeline([add1, add2, mult3])
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 9)

        x = Msg("user", "", "user", metadata={"result": 0})
        pipeline = SequentialPipeline([add1, mult3, add2])
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 5)

        x = Msg("user", "", "user", metadata={"result": 0})
        pipeline = SequentialPipeline([mult3, add1, add2])
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 3)


if __name__ == "__main__":
    unittest.main()
