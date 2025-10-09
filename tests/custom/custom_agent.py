# -*- coding: utf-8 -*-
"""A python module contains AgentBase subclasses.
For testing the agent dir loading functionality.
"""
from utils import speak  # pylint: disable=E0611
from agentscope.agent import AgentBase
from agentscope.message import Msg


class CustomAgent(AgentBase):
    """A customized agent class which import a function from another file"""

    def reply(self, x: Msg = None) -> Msg:
        return Msg(name=self.name, role="assistant", content=speak())
