# -*- coding: utf-8 -*-
""" Unit test for msghub."""
import unittest
from typing import Optional, Union, Sequence

from agentscope.agent import AgentBase
from agentscope import msghub
from agentscope.message import Msg


class TestAgent(AgentBase):
    """Test agent class for msghub."""

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        """Reply function for agent."""
        if x is not None:
            self.memory.add(x)
            return x
        else:
            return {}


class MsgHubTest(unittest.TestCase):
    """
    Test for MsgHub
    """

    def setUp(self) -> None:
        """Init for ExampleTest."""
        self.whisper = TestAgent("whisper")
        self.agent1 = TestAgent("agent1")
        self.agent2 = TestAgent("agent2")
        self.agent3 = TestAgent("agent3")

    def test_msghub_operation(self) -> None:
        """Test add, delete and broadcast operations"""
        msg1 = Msg(name="a1", content="msg1", role="assistant")
        msg2 = Msg(name="a2", content="msg2", role="assistant")
        msg3 = Msg(name="a3", content="msg3", role="assistant")
        msg4 = Msg(name="a4", content="msg4", role="assistant")

        with msghub(participants=[self.agent1, self.agent2]) as hub:
            self.agent1(msg1)
            self.agent2(msg2)

            hub.delete(self.agent1)

            hub.add(self.agent3)

            self.agent3(msg3)

            hub.broadcast(msg4)

        self.assertListEqual(
            self.agent2.memory.get_memory(),
            [
                msg1,
                msg2,
                msg3,
                msg4,
            ],
        )

        self.assertListEqual(self.agent1.memory.get_memory(), [msg1, msg2])

        self.assertListEqual(self.agent3.memory.get_memory(), [msg3, msg4])

    def test_msghub(self) -> None:
        """msghub test."""

        ground_truth = [
            Msg(
                name="w1",
                content="This secret that my password is 123456 can't be"
                " leaked!",
                role="assistant",
            ),
        ]

        with msghub(participants=[self.whisper, self.agent1, self.agent2]):
            self.whisper(ground_truth)

        # agent1 and agent2 heard whisper's secret!
        self.assertListEqual(
            self.agent1.memory.get_memory(),
            ground_truth,
        )

        self.assertListEqual(
            self.agent2.memory.get_memory(),
            ground_truth,
        )

        # agent3 didn't hear whisper's secret!
        self.assertListEqual(
            self.agent3.memory.get_memory(),
            [],
        )


if __name__ == "__main__":
    unittest.main()
