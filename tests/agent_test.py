# -*- coding: utf-8 -*-
"""
Unit tests for agent classes and functions
"""

import unittest
import uuid
from typing import Optional, Union

from agentscope.agents import AgentBase
from agentscope.message import Msg


class TestAgent(AgentBase):
    """An agent for test usage"""

    def __init__(
        self,
        name: str,
        sys_prompt: str = None,
        **kwargs: dict,
    ) -> None:
        super().__init__()
        self.agent_id = str(uuid.uuid4())
        self.name = name
        self.sys_prompt = sys_prompt
        self.model_config_name = kwargs.get("model_config")
        self.use_memory = kwargs.get("use_memory")

    def reply(self, x: Optional[Union[Msg, list[Msg]]] = None) -> Msg:
        return x


class TestAgentCopy(TestAgent):
    """A copy of testagent"""


class BasicAgentTest(unittest.TestCase):
    """Test cases for basic agents"""

    def test_agent_init(self) -> None:
        """Test the init of AgentBase subclass."""
        a1 = TestAgent(
            "a",
            "Hi",
            use_memory=False,  # type: ignore[arg-type]
            attribute_1="hello world",  # type: ignore[arg-type]
        )
        self.assertEqual(a1.name, "a")
        self.assertEqual(a1.sys_prompt, "Hi")
        self.assertEqual(a1.use_memory, False)
        self.assertEqual(a1.model_config_name, None)

        a2 = TestAgent(
            "b",
            sys_prompt="Hello",
            attribute_2="Bye",  # type: ignore[arg-type]
        )
        self.assertEqual(a2.name, "b")
        self.assertEqual(a2.sys_prompt, "Hello")
        self.assertEqual(a2.use_memory, None)
        self.assertEqual(a2.model_config_name, None)
        self.assertNotEqual(a1.agent_id, a2.agent_id)
        a3 = TestAgentCopy("c")
        self.assertNotEqual(a3.agent_id, a2.agent_id)
        a4 = TestAgent(
            "d",
        )
        a4.agent_id = "agent_id_for_d"  # pylint: disable=W0212
        self.assertEqual(a4.agent_id, "agent_id_for_d")
