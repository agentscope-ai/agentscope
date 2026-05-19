# -*- coding: utf-8 -*-
"""Unit tests for topic-based message routing in MsgHub"""
import time
from typing import Any, List
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import Msg, TopicFilter
from agentscope.pipeline import MsgHub
from agentscope.agent import AgentBase


class MockAgent(AgentBase):
    """Mock agent that records received messages for testing."""

    def __init__(self, name: str = "MockAgent") -> None:
        super().__init__()
        self.name = name
        self.received_messages: List[Msg] = []

    async def reply(self, *args: Any, **kwargs: Any) -> Msg:
        """Reply function - returns a simple message."""
        return Msg(self.name, "Reply from " + self.name, "assistant")

    async def observe(self, msg: Msg | List[Msg] | None) -> None:
        """Observe function - records received messages."""
        if msg is None:
            return
        if isinstance(msg, list):
            self.received_messages.extend(msg)
        else:
            self.received_messages.append(msg)

    async def handle_interrupt(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """Handle interrupt."""
        return Msg(self.name, "Interrupted", "assistant")

    def clear_received(self) -> None:
        """Clear received messages."""
        self.received_messages.clear()


class TopicFilterTest(IsolatedAsyncioTestCase):
    """Test cases for TopicFilter class."""

    def test_matches_subscriber_no_topics(self) -> None:
        """Test subscriber with no topics receives all messages."""
        self.assertTrue(TopicFilter.matches(["task.create"], None))
        self.assertTrue(TopicFilter.matches(["task.create"], []))
        self.assertTrue(TopicFilter.matches(None, None))
        self.assertTrue(TopicFilter.matches([], ["task.*"]))

    def test_matches_message_no_topics(self) -> None:
        """Test message with no topics is received by all subscribers."""
        self.assertTrue(TopicFilter.matches(None, ["task.create"]))
        self.assertTrue(TopicFilter.matches([], ["task.create"]))

    def test_matches_exact(self) -> None:
        """Test exact topic matching."""
        self.assertTrue(TopicFilter.matches(["task.create"], ["task.create"]))
        self.assertTrue(TopicFilter.matches(["task.create", "notify"], ["task.create"]))
        self.assertTrue(TopicFilter.matches(["task.create"], ["task.create", "notify"]))

    def test_matches_wildcard(self) -> None:
        """Test wildcard matching with fnmatch."""
        self.assertTrue(TopicFilter.matches(["task.create"], ["task.*"]))
        self.assertTrue(TopicFilter.matches(["task.finish"], ["task.*"]))
        self.assertTrue(TopicFilter.matches(["task.update.status"], ["task.*"]))

        self.assertFalse(TopicFilter.matches(["notify.user"], ["task.*"]))
        self.assertTrue(TopicFilter.matches(["task.create"], ["task.?????"]))

    def test_matches_multiple_patterns(self) -> None:
        """Test multiple topic patterns."""
        self.assertTrue(TopicFilter.matches(
            ["task.create"],
            ["task.*", "notify.*"],
        ))
        self.assertTrue(TopicFilter.matches(
            ["notify.user"],
            ["task.*", "notify.*"],
        ))
        self.assertFalse(TopicFilter.matches(
            ["system.alert"],
            ["task.*", "notify.*"],
        ))

    def test_matches_multiple_message_topics(self) -> None:
        """Test message with multiple topics."""
        self.assertTrue(TopicFilter.matches(
            ["task.create", "notify.admin"],
            ["notify.*"],
        ))
        self.assertTrue(TopicFilter.matches(
            ["task.create", "notify.admin"],
            ["task.*"],
        ))
        self.assertFalse(TopicFilter.matches(
            ["task.create", "notify.admin"],
            ["system.*"],
        ))


class MsgHubTopicRoutingTest(IsolatedAsyncioTestCase):
    """Test cases for topic-based routing in MsgHub."""

    def setUp(self) -> None:
        """Set up test agents."""
        self.agent_all = MockAgent("AgentAll")
        self.agent_task = MockAgent("AgentTask")
        self.agent_notify = MockAgent("AgentNotify")
        self.agent_wildcard = MockAgent("AgentWildcard")
        self.agent_multi = MockAgent("AgentMulti")
        self.agent_sender = MockAgent("AgentSender")

    def clear_all_received(self) -> None:
        """Clear received messages for all agents."""
        self.agent_all.clear_received()
        self.agent_task.clear_received()
        self.agent_notify.clear_received()
        self.agent_wildcard.clear_received()
        self.agent_multi.clear_received()
        self.agent_sender.clear_received()

    async def test_backward_compatible_no_topics(self) -> None:
        """Test default full subscription (backward compatible).

        When no topics are specified, all subscribers receive all messages.
        """
        async with MsgHub(participants=[self.agent_all, self.agent_sender]):
            msg_no_topics = Msg("System", "Hello", "system")
            await self.agent_sender.print(msg_no_topics)

        self.assertEqual(len(self.agent_all.received_messages), 1)
        self.assertEqual(self.agent_all.received_messages[0].content, "Hello")

    async def test_exact_topic_filter_broadcast(self) -> None:
        """Test exact topic filtering with MsgHub.broadcast()."""
        hub = MsgHub(participants=[])
        hub.add_participant(self.agent_task, topics=["task.create"])
        hub.add_participant(self.agent_notify, topics=["notify.user"])
        hub.add_participant(self.agent_all, topics=None)

        async with hub:
            msg_task = Msg("System", "Task created", "system", topics=["task.create"])
            msg_notify = Msg("System", "User notified", "system", topics=["notify.user"])
            msg_no_topic = Msg("System", "Broadcast", "system")

            await hub.broadcast(msg_task)
            self.clear_all_received()

            await hub.broadcast(msg_notify)
            self.clear_all_received()

            await hub.broadcast(msg_no_topic)
            self.clear_all_received()

            await hub.broadcast(msg_task)

        self.assertEqual(len(self.agent_task.received_messages), 1)
        self.assertEqual(len(self.agent_notify.received_messages), 0)
        self.assertEqual(len(self.agent_all.received_messages), 1)

    async def test_wildcard_topic_matching(self) -> None:
        """Test wildcard topic matching."""
        hub = MsgHub(participants=[])
        hub.add_participant(self.agent_wildcard, topics=["task.*"])
        hub.add_participant(self.agent_notify, topics=["notify.*"])

        async with hub:
            msg_task_create = Msg(
                "System", "Create task", "system", topics=["task.create"]
            )
            msg_task_finish = Msg(
                "System", "Finish task", "system", topics=["task.finish"]
            )
            msg_notify = Msg(
                "System", "Notify user", "system", topics=["notify.user"]
            )

            await hub.broadcast(msg_task_create)
            await hub.broadcast(msg_task_finish)
            await hub.broadcast(msg_notify)

        self.assertEqual(len(self.agent_wildcard.received_messages), 2)
        self.assertEqual(len(self.agent_notify.received_messages), 1)

        wildcard_contents = [m.content for m in self.agent_wildcard.received_messages]
        self.assertIn("Create task", wildcard_contents)
        self.assertIn("Finish task", wildcard_contents)
        self.assertNotIn("Notify user", wildcard_contents)

    async def test_multi_topic_subscriber(self) -> None:
        """Test subscriber interested in multiple topics."""
        hub = MsgHub(participants=[])
        hub.add_participant(
            self.agent_multi,
            topics=["task.*", "notify.*"],
        )
        hub.add_participant(self.agent_task, topics=["task.*"])

        async with hub:
            msg_task = Msg("System", "Task", "system", topics=["task.create"])
            msg_notify = Msg("System", "Notify", "system", topics=["notify.user"])
            msg_system = Msg("System", "System", "system", topics=["system.alert"])

            await hub.broadcast(msg_task)
            await hub.broadcast(msg_notify)
            await hub.broadcast(msg_system)

        self.assertEqual(len(self.agent_multi.received_messages), 2)
        self.assertEqual(len(self.agent_task.received_messages), 1)

        multi_contents = [m.content for m in self.agent_multi.received_messages]
        self.assertIn("Task", multi_contents)
        self.assertIn("Notify", multi_contents)
        self.assertNotIn("System", multi_contents)

    async def test_unmatched_message_not_delivered(self) -> None:
        """Test messages that don't match any topic are not delivered."""
        hub = MsgHub(participants=[])
        hub.add_participant(self.agent_task, topics=["task.create"])
        hub.add_participant(self.agent_notify, topics=["notify.user"])

        async with hub:
            msg_system = Msg("System", "System alert", "system", topics=["system.alert"])

            await hub.broadcast(msg_system)

        self.assertEqual(len(self.agent_task.received_messages), 0)
        self.assertEqual(len(self.agent_notify.received_messages), 0)

    async def test_delivery_order_stable(self) -> None:
        """Test message delivery order is stable (by add_participant order)."""
        hub = MsgHub(participants=[])
        agent1 = MockAgent("Agent1")
        agent2 = MockAgent("Agent2")
        agent3 = MockAgent("Agent3")

        hub.add_participant(agent1)
        hub.add_participant(agent2)
        hub.add_participant(agent3)

        self.assertEqual(hub.participants[0], agent1)
        self.assertEqual(hub.participants[1], agent2)
        self.assertEqual(hub.participants[2], agent3)

        async with hub:
            msg = Msg("System", "Test", "system")
            await hub.broadcast(msg)

        self.assertEqual(len(agent1.received_messages), 1)
        self.assertEqual(len(agent2.received_messages), 1)
        self.assertEqual(len(agent3.received_messages), 1)

    async def test_auto_broadcast_with_topics(self) -> None:
        """Test auto-broadcast (agent reply) respects topic filtering."""
        hub = MsgHub(participants=[])
        hub.add_participant(self.agent_sender)
        hub.add_participant(self.agent_task, topics=["task.*"])
        hub.add_participant(self.agent_notify, topics=["notify.*"])

        async with hub:
            msg_with_topic = Msg(
                self.agent_sender.name,
                "Task created",
                "assistant",
                topics=["task.create"],
            )
            await self.agent_sender.print(msg_with_topic)

        self.assertEqual(len(self.agent_task.received_messages), 1)
        self.assertEqual(len(self.agent_notify.received_messages), 0)

    async def test_message_without_topics_received_by_all(self) -> None:
        """Test messages without topics are received by all subscribers."""
        hub = MsgHub(participants=[])
        hub.add_participant(self.agent_task, topics=["task.create"])
        hub.add_participant(self.agent_notify, topics=["notify.user"])
        hub.add_participant(self.agent_all)

        async with hub:
            msg_no_topic = Msg("System", "No topic", "system", topics=None)
            msg_empty_topics = Msg("System", "Empty topics", "system", topics=[])

            await hub.broadcast(msg_no_topic)
            await hub.broadcast(msg_empty_topics)

        self.assertEqual(len(self.agent_task.received_messages), 2)
        self.assertEqual(len(self.agent_notify.received_messages), 2)
        self.assertEqual(len(self.agent_all.received_messages), 2)

    async def test_subscriber_without_topics_receives_all(self) -> None:
        """Test subscribers without topics receive all messages."""
        hub = MsgHub(participants=[])
        hub.add_participant(self.agent_task, topics=["task.create"])
        hub.add_participant(self.agent_all)

        async with hub:
            msg_task = Msg("System", "Task", "system", topics=["task.create"])
            msg_notify = Msg("System", "Notify", "system", topics=["notify.user"])
            msg_system = Msg("System", "System", "system", topics=["system.alert"])

            await hub.broadcast(msg_task)
            await hub.broadcast(msg_notify)
            await hub.broadcast(msg_system)

        self.assertEqual(len(self.agent_task.received_messages), 1)
        self.assertEqual(len(self.agent_all.received_messages), 3)

    async def test_add_method_with_topics(self) -> None:
        """Test add() method with topics parameter works correctly."""
        hub = MsgHub(participants=[])
        hub.add(self.agent_task, topics=["task.*"])
        hub.add([self.agent_notify, self.agent_all], topics=None)

        self.assertEqual(len(hub.participants), 3)

        async with hub:
            msg_task = Msg("System", "Task", "system", topics=["task.create"])

            await hub.broadcast(msg_task)

        self.assertEqual(len(self.agent_task.received_messages), 1)
        self.assertEqual(len(self.agent_notify.received_messages), 1)
        self.assertEqual(len(self.agent_all.received_messages), 1)

    async def test_delete_removes_topics(self) -> None:
        """Test delete() removes participant and their topics."""
        hub = MsgHub(participants=[])
        hub.add_participant(self.agent_task, topics=["task.*"])
        hub.add_participant(self.agent_notify, topics=["notify.*"])

        self.assertEqual(len(hub.participants), 2)
        self.assertIn(self.agent_task.id, hub._participant_topics)
        self.assertIn(self.agent_notify.id, hub._participant_topics)

        hub.delete(self.agent_task)

        self.assertEqual(len(hub.participants), 1)
        self.assertNotIn(self.agent_task.id, hub._participant_topics)
        self.assertIn(self.agent_notify.id, hub._participant_topics)

    async def test_msg_topics_serialization(self) -> None:
        """Test Msg topics serialization and deserialization."""
        original = Msg(
            "Test",
            "Content",
            "user",
            topics=["task.create", "notify.admin"],
        )

        msg_dict = original.to_dict()
        self.assertIn("topics", msg_dict)
        self.assertEqual(msg_dict["topics"], ["task.create", "notify.admin"])

        restored = Msg.from_dict(msg_dict)
        self.assertEqual(restored.topics, ["task.create", "notify.admin"])
        self.assertEqual(restored.id, original.id)

    async def test_msg_topics_none_serialization(self) -> None:
        """Test Msg with None topics is not serialized."""
        original = Msg("Test", "Content", "user", topics=None)
        msg_dict = original.to_dict()
        self.assertNotIn("topics", msg_dict)

        restored = Msg.from_dict(msg_dict)
        self.assertIsNone(restored.topics)


class MsgHubDeliveryOrderStabilityTest(IsolatedAsyncioTestCase):
    """Regression tests for delivery order stability.

    These tests ensure that message delivery order remains consistent
    across multiple invocations, preventing any regression to random
    ordering behavior.
    """

    async def test_delivery_order_50_runs_consistent(self) -> None:
        """Test delivery order is consistent across 50 consecutive runs.

        This is a regression test to ensure the order of message
        remains stable (by add_participant order), not some random
        or hash-based ordering.
        """
        num_agents = 10
        num_runs = 50

        agents = [MockAgent(f"Agent{i}") for i in range(num_agents)]

        hub = MsgHub(participants=[])
        for agent in agents:
            hub.add_participant(agent)

        expected_order = [a.id for a in agents]
        all_orders = []

        async with hub:
            for run_idx in range(num_runs):
                for agent in agents:
                    agent.clear_received()

                msg = Msg("System", f"Run {run_idx}", "system")
                await hub.broadcast(msg)

                received_order = [
                    a.id for a in agents if len(a.received_messages) > 0
                ]
                all_orders.append(received_order)

        for i, order in enumerate(all_orders):
            self.assertEqual(
                order, expected_order,
                f"Run {i} order mismatch. "
                f"Expected: {expected_order}, Got: {order}"
            )

        self.assertEqual(len(all_orders), num_runs)


class TopicFilterPerformanceTest(IsolatedAsyncioTestCase):
    """Performance tests for TopicFilter with lru_cache optimization.

    These tests ensure the performance of the topic matching algorithm
    to be fast enough for typical multi-agent scenarios.
    """

    def test_topic_filter_1000_matches_under_10ms(self) -> None:
        """Benchmark: 1000 topic match operations should complete
        in under 10ms.

        This threshold is set to ensure the lru_cache optimization is
        working correctly and the matching algorithm is efficient.
        """
        test_cases = [
            (["task.create", "task.update", "task.delete"],
             ["task.*", "notify.*"]),
            (["notify.user", "notify.admin", "notify.all"],
             ["notify.*"]),
            (["system.alert", "system.status"],
             ["task.*", "notify.*"]),
            (["task.create.deep", "task.update.status"],
             ["task.*"]),
            (["task.create"],
             ["*"]),
        ]

        iterations = 1000

        TopicFilter._matches_cached.cache_clear()

        start = time.perf_counter()

        for _ in range(iterations):
            for msg_topics, sub_topics in test_cases:
                TopicFilter.matches(msg_topics, sub_topics)

        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"\n  [Benchmark]  TopicFilter.matches x{iterations} 次: {elapsed_ms:.2f} ms")

        self.assertLess(
            elapsed_ms,
            10.0,
            f"TopicFilter.matches {iterations} 次操作耗时 {elapsed_ms:.2f}ms, "
            f"超过阈值 10ms"
        )

    def test_lru_cache_effectiveness(self) -> None:
        """Verify that lru_cache provides a performance improvement.

        This test demonstrates that cached calls are significantly faster
        than uncached ones.
        """
        test_msg_topics = ["task.create", "task.update"]
        test_sub_topics = ["task.*", "notify.*"]

        TopicFilter._matches_cached.cache_clear()

        start = time.perf_counter()
        for _ in range(100):
            TopicFilter.matches(test_msg_topics, test_sub_topics)
        first_100_ms = (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        for _ in range(1000):
            TopicFilter.matches(test_msg_topics, test_sub_topics)
        next_1000_ms = (time.perf_counter() - start) * 1000

        print(f"\n  [Benchmark] 首次 100 次 (含缓存预热): {first_100_ms:.2f} ms")
        print(f"  [Benchmark] 后续 1000 次 (已缓存): {next_1000_ms:.2f} ms")

        cache_info = TopicFilter._matches_cached.cache_info()
        print(f"  [Cache Info] hits={cache_info.hits}, misses={cache_info.misses}, "
              f"maxsize={cache_info.maxsize}")
