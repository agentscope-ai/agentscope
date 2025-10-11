# -*- coding: utf-8 -*-
"""Unit tests for pipeline classes and functions"""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import Msg
from agentscope.pipeline import (
    SequentialPipeline,
    FanoutPipeline,
    sequential_pipeline,
    fanout_pipeline,
    stream_printing_messages,
)

from agentscope.agent import AgentBase


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


class StreamAgent(AgentBase):
    """Add agent class."""

    def __init__(self) -> None:
        """Initialize the agent"""
        super().__init__()
        self.name = "Stream"

    async def reply(self) -> Msg | None:
        """Reply function"""
        await self.print(
            Msg(
                self.name,
                "123",
                "user",
            ),
        )
        await self.print(
            Msg(
                "user",
                "456",
                "user",
            ),
        )
        await self.print(
            Msg(
                self.name,
                "789",
                "user",
            ),
        )
        return None

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


class PipelineTest(IsolatedAsyncioTestCase):
    """Test cases for Pipelines"""

    async def test_functional_sequential_pipeline(self) -> None:
        """Test SequentialPipeline executes agents sequentially"""

        add1 = AddAgent(1)
        add2 = AddAgent(2)
        mult3 = MultAgent(3)

        x = Msg("user", "", "user", metadata={"result": 0})
        res = await sequential_pipeline([add1, add2, mult3], x)
        self.assertEqual(9, res.metadata["result"])

        x = Msg("user", "", "user", metadata={"result": 0})
        res = await sequential_pipeline([add1, mult3, add2], x)
        self.assertEqual(5, res.metadata["result"])

        x = Msg("user", "", "user", metadata={"result": 0})
        res = await sequential_pipeline([mult3, add1, add2], x)
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

    async def test_functional_sequential_pipeline_with_none_message(
        self,
    ) -> None:
        """Test functional sequential pipeline with None message input"""

        add1 = AddAgent(1)
        add2 = AddAgent(2)
        mult3 = MultAgent(3)

        # Test with None input
        res = await sequential_pipeline([add1, add2, mult3], None)
        self.assertIsNone(res)
        # Test with empty agent list and None input
        res = await sequential_pipeline([], None)
        self.assertIsNone(res)

    async def test_class_sequential_pipeline_with_none_message(self) -> None:
        """Test class-based sequential pipeline with None message input"""

        add1 = AddAgent(1)
        add2 = AddAgent(2)
        mult3 = MultAgent(3)

        # Test with None input
        pipeline = SequentialPipeline([add1, add2, mult3])
        res = await pipeline(None)
        self.assertIsNone(res)

        # Test with empty agent list and None input
        empty_pipeline = SequentialPipeline([])
        res = await empty_pipeline(None)
        self.assertIsNone(res)

    async def test_empty_agent_list(self) -> None:
        """Test pipeline with empty agent list"""

        x = Msg("user", "", "user", metadata={"result": 42})

        # Functional pipeline
        res = await sequential_pipeline([], x)
        self.assertEqual(res.metadata["result"], 42)
        self.assertEqual(res, x)  # Should return the same message object

        # Class-based pipeline
        pipeline = SequentialPipeline([])
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 42)
        self.assertEqual(res, x)  # Should return the same message object

    async def test_single_agent_pipeline(
        self,
    ) -> None:
        """Test pipeline with single agent"""

        add1 = AddAgent(5)

        x = Msg("user", "", "user", metadata={"result": 10})

        # Functional pipeline
        res = await sequential_pipeline([add1], x)
        self.assertEqual(res.metadata["result"], 15)

        # Class-based pipeline
        pipeline = SequentialPipeline([add1])
        x = Msg("user", "", "user", metadata={"result": 10})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 15)

        # Test single agent with None input
        res = await sequential_pipeline([add1], None)
        self.assertIsNone(res)
        res = await pipeline(None)
        self.assertIsNone(res)

    # ==================== Fanout Pipeline Tests ====================

    async def test_functional_fanout_pipeline_concurrent(self) -> None:
        """Test fanout_pipeline executes agents concurrently with
        independent inputs"""

        add1 = AddAgent(1)
        add2 = AddAgent(2)
        mult3 = MultAgent(3)

        x = Msg("user", "", "user", metadata={"result": 0})
        res = await fanout_pipeline([add1, add2, mult3], x, enable_gather=True)
        # Each agent should process the original input independently
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0].metadata["result"], 1)  # 0 + 1
        self.assertEqual(res[1].metadata["result"], 2)  # 0 + 2
        self.assertEqual(res[2].metadata["result"], 0)  # 0 * 3

        # Test different order
        x = Msg("user", "", "user", metadata={"result": 0})
        res = await fanout_pipeline([mult3, add1, add2], x, enable_gather=True)
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0].metadata["result"], 0)  # 0 * 3
        self.assertEqual(res[1].metadata["result"], 1)  # 0 + 1
        self.assertEqual(res[2].metadata["result"], 2)  # 0 + 2

    async def test_functional_fanout_pipeline_sequential(self) -> None:
        """Test fanout_pipeline executes agents sequentially with
        independent inputs"""

        add1 = AddAgent(1)
        add2 = AddAgent(2)
        mult3 = MultAgent(3)

        x = Msg("user", "", "user", metadata={"result": 0})
        res = await fanout_pipeline(
            [add1, add2, mult3],
            x,
            enable_gather=False,
        )

        # Each agent should still process the original input independently
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0].metadata["result"], 1)  # 0 + 1
        self.assertEqual(res[1].metadata["result"], 2)  # 0 + 2
        self.assertEqual(res[2].metadata["result"], 0)  # 0 * 3

    async def test_class_fanout_pipeline_concurrent(self) -> None:
        """Test FanoutPipeline class with concurrent execution"""

        add1 = AddAgent(1)
        add2 = AddAgent(2)
        mult3 = MultAgent(3)

        x = Msg("user", "", "user", metadata={"result": 0})
        pipeline = FanoutPipeline([add1, add2, mult3], enable_gather=True)
        res = await pipeline(x)
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0].metadata["result"], 1)  # 0 + 1
        self.assertEqual(res[1].metadata["result"], 2)  # 0 + 2
        self.assertEqual(res[2].metadata["result"], 0)  # 0 * 3

    async def test_class_fanout_pipeline_sequential(self) -> None:
        """Test FanoutPipeline class with sequential execution"""

        add1 = AddAgent(1)
        add2 = AddAgent(2)
        mult3 = MultAgent(3)

        x = Msg("user", "", "user", metadata={"result": 0})
        pipeline = FanoutPipeline([add1, add2, mult3], enable_gather=False)
        res = await pipeline(x)

        self.assertEqual(len(res), 3)
        self.assertEqual(res[0].metadata["result"], 1)  # 0 + 1
        self.assertEqual(res[1].metadata["result"], 2)  # 0 + 2
        self.assertEqual(res[2].metadata["result"], 0)  # 0 * 3

    async def test_fanout_pipeline_empty_agents(self) -> None:
        """Test fanout pipeline with empty agent list"""

        x = Msg("user", "", "user", metadata={"result": 42})

        # Functional pipeline
        res = await fanout_pipeline([], x)
        self.assertEqual(res, [])

        res = await fanout_pipeline([], x, enable_gather=False)
        self.assertEqual(res, [])

        # Class-based pipeline
        pipeline = FanoutPipeline([])
        res = await pipeline(x)
        self.assertEqual(res, [])

    async def test_fanout_pipeline_with_none_message(self) -> None:
        """Test fanout pipeline with None message input"""

        add1 = AddAgent(1)
        add2 = AddAgent(2)

        # Functional pipeline
        res = await fanout_pipeline([add1, add2], None)
        self.assertEqual(len(res), 2)
        self.assertIsNone(res[0])
        self.assertIsNone(res[1])

        # Class-based pipeline
        pipeline = FanoutPipeline([add1, add2])
        res = await pipeline(None)
        self.assertEqual(len(res), 2)
        self.assertIsNone(res[0])
        self.assertIsNone(res[1])

    async def test_stream_printing_messages(self) -> None:
        """Test stream_printing_messages function"""

        agent = StreamAgent()

        i = 0
        async for msg, last in stream_printing_messages(
            [agent],
            agent(),
        ):
            self.assertTrue(last)

            if i == 0:
                self.assertEqual(
                    msg.content,
                    "123",
                )

            elif i == 1:
                self.assertEqual(
                    msg.content,
                    "456",
                )

            elif i == 2:
                self.assertEqual(
                    msg.content,
                    "789",
                )

            i += 1
