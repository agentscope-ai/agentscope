# -*- coding: utf-8 -*-
"""Unit tests for branching pipeline classes"""
from typing import Any, List
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import Msg
from agentscope.pipeline import (
    IfElsePipeline,
    SwitchPipeline,
    ParallelBranchPipeline,
    SequentialPipeline,
)

from agentscope.agent import AgentBase


class AddAgent(AgentBase):
    """Add agent class."""

    def __init__(self, value: int, name: str = "Add") -> None:
        """Initialize the agent"""
        super().__init__()
        self.name = name
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

    def __init__(self, value: int, name: str = "Mult") -> None:
        """Initialize the agent"""
        super().__init__()
        self.name = name
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


class ErrorAgent(AgentBase):
    """Agent that raises an error during execution."""

    def __init__(self, error_msg: str = "Test error", name: str = "Error") -> None:
        """Initialize the agent"""
        super().__init__()
        self.name = name
        self.error_msg = error_msg

    async def reply(self, x: Msg | None) -> Msg | None:
        """Reply function that raises an error"""
        raise ValueError(self.error_msg)

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Observe function"""

    async def handle_interrupt(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """Handle interrupt"""


class ConsistentMsgAgent(AgentBase):
    """Agent that returns Msg with consistent structure."""

    def __init__(self, name: str, role: str, content_prefix: str) -> None:
        """Initialize the agent"""
        super().__init__()
        self.name = name
        self.role = role
        self.content_prefix = content_prefix

    async def reply(self, x: Msg | None) -> Msg | None:
        """Reply function returning consistent Msg structure"""
        if x is None:
            return None
        return Msg(
            name=self.name,
            content=f"{self.content_prefix}: {x.content}",
            role=self.role,
            metadata={"processed": True, "original_id": x.id},
        )

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Observe function"""

    async def handle_interrupt(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """Handle interrupt"""


class NestedParallelAgent(AgentBase):
    """Agent that internally uses ParallelBranchPipeline for testing deep nesting."""

    def __init__(self, name: str, inner_agents: List[AgentBase]) -> None:
        """Initialize the agent"""
        super().__init__()
        self.name = name
        self.inner_pipeline = ParallelBranchPipeline(*inner_agents)

    async def reply(self, x: Msg | None) -> Msg | None:
        """Reply function that executes nested parallel pipeline"""
        if x is None:
            return None
        results = await self.inner_pipeline(x)
        total = sum(r.metadata.get("result", 0) for r in results if hasattr(r, "metadata"))
        return Msg(
            name=self.name,
            content=f"Nested results: {len(results)}",
            role="assistant",
            metadata={"result": total, "num_results": len(results)},
        )

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Observe function"""

    async def handle_interrupt(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """Handle interrupt"""


class BranchingPipelineTest(IsolatedAsyncioTestCase):
    """Test cases for branching pipelines"""

    # ==================== IfElsePipeline Tests ====================

    async def test_ifelse_pipeline_true_branch(self) -> None:
        """Test IfElsePipeline executes true branch when condition is True"""

        def is_positive(msg: Msg) -> bool:
            return msg.metadata.get("result", 0) > 0

        add5 = AddAgent(5, name="Add5")
        mult10 = MultAgent(10, name="Mult10")

        pipeline = IfElsePipeline(is_positive, add5, mult10)

        x = Msg("user", "", "user", metadata={"result": 10})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 15)  # 10 + 5

    async def test_ifelse_pipeline_false_branch(self) -> None:
        """Test IfElsePipeline executes false branch when condition is False"""

        def is_positive(msg: Msg) -> bool:
            return msg.metadata.get("result", 0) > 0

        add5 = AddAgent(5, name="Add5")
        mult10 = MultAgent(10, name="Mult10")

        pipeline = IfElsePipeline(is_positive, add5, mult10)

        x = Msg("user", "", "user", metadata={"result": -10})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], -100)  # -10 * 10

    async def test_ifelse_pipeline_async_condition(self) -> None:
        """Test IfElsePipeline with async condition function"""

        async def is_positive_async(msg: Msg) -> bool:
            return msg.metadata.get("result", 0) > 0

        add5 = AddAgent(5, name="Add5")
        mult10 = MultAgent(10, name="Mult10")

        pipeline = IfElsePipeline(is_positive_async, add5, mult10)

        x = Msg("user", "", "user", metadata={"result": 10})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 15)  # 10 + 5

        x = Msg("user", "", "user", metadata={"result": -10})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], -100)  # -10 * 10

    async def test_ifelse_pipeline_none_message(self) -> None:
        """Test IfElsePipeline with None message input"""

        def is_positive(msg: Msg) -> bool:
            return msg.metadata.get("result", 0) > 0

        add5 = AddAgent(5, name="Add5")
        mult10 = MultAgent(10, name="Mult10")

        pipeline = IfElsePipeline(is_positive, add5, mult10)

        res = await pipeline(None)
        self.assertIsNone(res)

    # ==================== SwitchPipeline Tests ====================

    async def test_switch_pipeline_hit_case(self) -> None:
        """Test SwitchPipeline executes the correct case when key matches"""

        def get_operation(msg: Msg) -> str:
            return msg.metadata.get("operation", "unknown")

        add5 = AddAgent(5, name="Add5")
        mult10 = MultAgent(10, name="Mult10")
        default = AddAgent(0, name="Default")

        pipeline = SwitchPipeline(
            key_fn=get_operation,
            cases={
                "add": add5,
                "multiply": mult10,
            },
            default=default,
        )

        x = Msg("user", "", "user", metadata={"result": 10, "operation": "add"})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 15)  # 10 + 5

        x = Msg("user", "", "user", metadata={"result": 10, "operation": "multiply"})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 100)  # 10 * 10

    async def test_switch_pipeline_default(self) -> None:
        """Test SwitchPipeline uses default when key doesn't match any case"""

        def get_operation(msg: Msg) -> str:
            return msg.metadata.get("operation", "unknown")

        add5 = AddAgent(5, name="Add5")
        mult10 = MultAgent(10, name="Mult10")
        default = AddAgent(100, name="Default")

        pipeline = SwitchPipeline(
            key_fn=get_operation,
            cases={
                "add": add5,
                "multiply": mult10,
            },
            default=default,
        )

        x = Msg("user", "", "user", metadata={"result": 10, "operation": "subtract"})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 110)  # 10 + 100

    async def test_switch_pipeline_no_default_key_error(self) -> None:
        """Test SwitchPipeline raises KeyError when key doesn't match and no default"""

        def get_operation(msg: Msg) -> str:
            return msg.metadata.get("operation", "unknown")

        add5 = AddAgent(5, name="Add5")
        mult10 = MultAgent(10, name="Mult10")

        pipeline = SwitchPipeline(
            key_fn=get_operation,
            cases={
                "add": add5,
                "multiply": mult10,
            },
            default=None,
        )

        x = Msg("user", "", "user", metadata={"result": 10, "operation": "subtract"})

        with self.assertRaises(KeyError) as context:
            await pipeline(x)

        self.assertIn("subtract", str(context.exception))

    async def test_switch_pipeline_async_key_fn(self) -> None:
        """Test SwitchPipeline with async key function"""

        async def get_operation_async(msg: Msg) -> str:
            return msg.metadata.get("operation", "unknown")

        add5 = AddAgent(5, name="Add5")
        mult10 = MultAgent(10, name="Mult10")
        default = AddAgent(0, name="Default")

        pipeline = SwitchPipeline(
            key_fn=get_operation_async,
            cases={
                "add": add5,
                "multiply": mult10,
            },
            default=default,
        )

        x = Msg("user", "", "user", metadata={"result": 10, "operation": "add"})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 15)  # 10 + 5

    async def test_switch_pipeline_none_message(self) -> None:
        """Test SwitchPipeline with None message input"""

        def get_operation(msg: Msg) -> str:
            return msg.metadata.get("operation", "unknown")

        add5 = AddAgent(5, name="Add5")
        mult10 = MultAgent(10, name="Mult10")

        pipeline = SwitchPipeline(
            key_fn=get_operation,
            cases={
                "add": add5,
                "multiply": mult10,
            },
        )

        res = await pipeline(None)
        self.assertIsNone(res)

    # ==================== ParallelBranchPipeline Tests ====================

    async def test_parallel_branch_pipeline_collect_results(self) -> None:
        """Test ParallelBranchPipeline collects results from all agents"""

        add1 = AddAgent(1, name="Add1")
        add2 = AddAgent(2, name="Add2")
        mult3 = MultAgent(3, name="Mult3")

        pipeline = ParallelBranchPipeline(add1, add2, mult3)

        x = Msg("user", "", "user", metadata={"result": 0})
        res = await pipeline(x)

        self.assertEqual(len(res), 3)
        self.assertEqual(res[0].metadata["result"], 1)  # 0 + 1
        self.assertEqual(res[1].metadata["result"], 2)  # 0 + 2
        self.assertEqual(res[2].metadata["result"], 0)  # 0 * 3

    async def test_parallel_branch_pipeline_one_failure(self) -> None:
        """Test ParallelBranchPipeline handles one agent failure without affecting others"""

        add1 = AddAgent(1, name="Add1")
        error_agent = ErrorAgent(error_msg="Intentional error", name="Error")
        mult3 = MultAgent(3, name="Mult3")

        pipeline = ParallelBranchPipeline(add1, error_agent, mult3)

        x = Msg("user", "", "user", metadata={"result": 0})
        res = await pipeline(x)

        self.assertEqual(len(res), 3)
        self.assertEqual(res[0].metadata["result"], 1)  # 0 + 1 (success)
        self.assertIsInstance(res[1], ValueError)  # Error agent raised exception
        self.assertEqual(str(res[1]), "Intentional error")
        self.assertEqual(res[2].metadata["result"], 0)  # 0 * 3 (success)

    async def test_parallel_branch_pipeline_empty_agents(self) -> None:
        """Test ParallelBranchPipeline with empty agent list returns []"""

        pipeline = ParallelBranchPipeline()

        x = Msg("user", "", "user", metadata={"result": 42})
        res = await pipeline(x)

        self.assertEqual(res, [])

    async def test_parallel_branch_pipeline_none_message(self) -> None:
        """Test ParallelBranchPipeline with None message input"""

        add1 = AddAgent(1, name="Add1")
        add2 = AddAgent(2, name="Add2")

        pipeline = ParallelBranchPipeline(add1, add2)

        res = await pipeline(None)

        self.assertEqual(len(res), 2)
        self.assertIsNone(res[0])
        self.assertIsNone(res[1])

    # ==================== Nested Pipeline Tests ====================

    async def test_nested_ifelse_in_ifelse(self) -> None:
        """Test IfElsePipeline containing IfElsePipeline in branches (nested conditions)

        Scenario:
        - Outer condition: is number > 10?
          - True branch: inner condition: is number > 50?
            - True: add 100
            - False: add 10
          - False branch: multiply by 2
        """

        def is_gt_10(msg: Msg) -> bool:
            return msg.metadata.get("result", 0) > 10

        def is_gt_50(msg: Msg) -> bool:
            return msg.metadata.get("result", 0) > 50

        add100 = AddAgent(100, name="Add100")
        add10 = AddAgent(10, name="Add10")
        mult2 = MultAgent(2, name="Mult2")

        inner_pipeline = IfElsePipeline(is_gt_50, add100, add10)
        outer_pipeline = IfElsePipeline(is_gt_10, inner_pipeline, mult2)

        x = Msg("user", "", "user", metadata={"result": 5})
        res = await outer_pipeline(x)
        self.assertEqual(res.metadata["result"], 10)  # 5 * 2

        x = Msg("user", "", "user", metadata={"result": 20})
        res = await outer_pipeline(x)
        self.assertEqual(res.metadata["result"], 30)  # 20 + 10

        x = Msg("user", "", "user", metadata={"result": 100})
        res = await outer_pipeline(x)
        self.assertEqual(res.metadata["result"], 200)  # 100 + 100

    async def test_ifelse_with_parallel_branch(self) -> None:
        """Test IfElsePipeline with ParallelBranchPipeline as branches

        Scenario:
        - Condition: is urgent?
          - True branch: parallel execution of multiple urgent handlers
          - False branch: parallel execution of normal handlers
        """

        def is_urgent(msg: Msg) -> bool:
            return msg.metadata.get("urgent", False)

        add1 = AddAgent(1, name="Urgent1")
        add2 = AddAgent(2, name="Urgent2")
        mult2 = MultAgent(2, name="Normal1")
        mult3 = MultAgent(3, name="Normal2")

        urgent_parallel = ParallelBranchPipeline(add1, add2)
        normal_parallel = ParallelBranchPipeline(mult2, mult3)

        pipeline = IfElsePipeline(is_urgent, urgent_parallel, normal_parallel)

        x = Msg("user", "test", "user", metadata={"result": 10, "urgent": True})
        res = await pipeline(x)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0].metadata["result"], 11)  # 10 + 1
        self.assertEqual(res[1].metadata["result"], 12)  # 10 + 2

        x = Msg("user", "test", "user", metadata={"result": 10, "urgent": False})
        res = await pipeline(x)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0].metadata["result"], 20)  # 10 * 2
        self.assertEqual(res[1].metadata["result"], 30)  # 10 * 3

    async def test_switch_with_sequential_pipeline(self) -> None:
        """Test SwitchPipeline with SequentialPipeline as a case

        Scenario:
        - Key: operation type
          - "complex": sequential pipeline (add 5, then multiply by 2, then add 10)
          - "simple": just add 1
          - default: just return original
        """

        def get_operation_type(msg: Msg) -> str:
            return msg.metadata.get("op_type", "simple")

        add5 = AddAgent(5, name="Add5")
        mult2 = MultAgent(2, name="Mult2")
        add10 = AddAgent(10, name="Add10")
        complex_seq = SequentialPipeline([add5, mult2, add10])

        add1 = AddAgent(1, name="Add1")

        class NoopAgent(AgentBase):
            def __init__(self):
                super().__init__()
                self.name = "Noop"

            async def reply(self, x: Msg | None) -> Msg | None:
                return x

            async def observe(self, msg: Msg | list[Msg] | None) -> None:
                pass

            async def handle_interrupt(self, *args, **kwargs) -> Msg:
                pass

        noop = NoopAgent()

        pipeline = SwitchPipeline(
            key_fn=get_operation_type,
            cases={
                "complex": complex_seq,
                "simple": add1,
            },
            default=noop,
        )

        x = Msg("user", "", "user", metadata={"result": 10, "op_type": "simple"})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 11)  # 10 + 1

        x = Msg("user", "", "user", metadata={"result": 10, "op_type": "complex"})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 40)  # ((10 + 5) * 2) + 10 = 40

        x = Msg("user", "", "user", metadata={"result": 10, "op_type": "unknown"})
        res = await pipeline(x)
        self.assertEqual(res.metadata["result"], 10)  # unchanged

    # ==================== Message Consistency Tests ====================

    async def test_ifelse_message_type_consistency(self) -> None:
        """Test IfElsePipeline branches return Msg with consistent type structure"""

        def is_question(msg: Msg) -> bool:
            return "?" in msg.content

        agent_question = ConsistentMsgAgent(
            name="QuestionHandler",
            role="assistant",
            content_prefix="Answered",
        )
        agent_statement = ConsistentMsgAgent(
            name="StatementHandler",
            role="assistant",
            content_prefix="Acknowledged",
        )

        pipeline = IfElsePipeline(is_question, agent_question, agent_statement)

        msg_question = Msg("user", "What time is it?", "user")
        res1 = await pipeline(msg_question)
        self.assertIsInstance(res1, Msg)
        self.assertEqual(res1.name, "QuestionHandler")
        self.assertEqual(res1.role, "assistant")
        self.assertIn("processed", res1.metadata)

        msg_statement = Msg("user", "The sky is blue.", "user")
        res2 = await pipeline(msg_statement)
        self.assertIsInstance(res2, Msg)
        self.assertEqual(res2.name, "StatementHandler")
        self.assertEqual(res2.role, "assistant")
        self.assertIn("processed", res2.metadata)

        self.assertEqual(type(res1), type(res2))
        self.assertEqual(res1.role, res2.role)
        self.assertIsNotNone(res1.id)
        self.assertIsNotNone(res2.id)

    async def test_switch_message_type_consistency(self) -> None:
        """Test SwitchPipeline cases return Msg with consistent type structure"""

        def get_topic(msg: Msg) -> str:
            if "weather" in msg.content.lower():
                return "weather"
            elif "news" in msg.content.lower():
                return "news"
            return "general"

        weather_agent = ConsistentMsgAgent(
            name="WeatherBot",
            role="assistant",
            content_prefix="Weather update",
        )
        news_agent = ConsistentMsgAgent(
            name="NewsBot",
            role="assistant",
            content_prefix="News update",
        )
        general_agent = ConsistentMsgAgent(
            name="GeneralBot",
            role="assistant",
            content_prefix="General response",
        )

        pipeline = SwitchPipeline(
            key_fn=get_topic,
            cases={
                "weather": weather_agent,
                "news": news_agent,
            },
            default=general_agent,
        )

        msg_weather = Msg("user", "What's the weather today?", "user")
        res1 = await pipeline(msg_weather)
        self.assertIsInstance(res1, Msg)
        self.assertEqual(res1.role, "assistant")
        self.assertIn("processed", res1.metadata)

        msg_news = Msg("user", "Tell me the news.", "user")
        res2 = await pipeline(msg_news)
        self.assertIsInstance(res2, Msg)
        self.assertEqual(res2.role, "assistant")
        self.assertIn("processed", res2.metadata)

        msg_general = Msg("user", "Hello there!", "user")
        res3 = await pipeline(msg_general)
        self.assertIsInstance(res3, Msg)
        self.assertEqual(res3.role, "assistant")
        self.assertIn("processed", res3.metadata)

        self.assertEqual(type(res1), type(res2))
        self.assertEqual(type(res2), type(res3))
        self.assertEqual(res1.role, res2.role)
        self.assertEqual(res2.role, res3.role)

    # ==================== Deep Nested Parallel Tests ====================

    async def test_deep_nested_parallel_no_deadlock(self) -> None:
        """Test deeply nested ParallelBranchPipeline doesn't cause deadlock

        Scenario:
        - Level 2: ParallelBranchPipeline with AddAgent(1) and AddAgent(2)
        - Level 1: NestedParallelAgent that wraps the Level 2 pipeline
        - Level 0: ParallelBranchPipeline with two NestedParallelAgents

        This tests depth >= 2 nesting without deadlock.
        """
        import asyncio

        add1 = AddAgent(1, name="L2_Add1")
        add2 = AddAgent(2, name="L2_Add2")

        nested_agent1 = NestedParallelAgent(name="L1_Agent1", inner_agents=[add1, add2])
        nested_agent2 = NestedParallelAgent(name="L1_Agent2", inner_agents=[add1, add2])

        top_level_pipeline = ParallelBranchPipeline(nested_agent1, nested_agent2)

        x = Msg("user", "test", "user", metadata={"result": 10})

        async def run_with_timeout():
            try:
                return await asyncio.wait_for(top_level_pipeline(x), timeout=5.0)
            except asyncio.TimeoutError:
                self.fail("Deep nested parallel execution timed out - possible deadlock")

        res = await run_with_timeout()

        self.assertEqual(len(res), 2)
        self.assertEqual(res[0].metadata["result"], 23)  # (10+1) + (10+2) = 23
        self.assertEqual(res[1].metadata["result"], 23)  # same
        self.assertEqual(res[0].metadata["num_results"], 2)
        self.assertEqual(res[1].metadata["num_results"], 2)
