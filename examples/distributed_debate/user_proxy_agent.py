# -*- coding: utf-8 -*-
"""User Proxy Agent class for distributed usage"""
from typing import Sequence, Union, Type
from typing import Optional

from pydantic import BaseModel

from agentscope.agent import UserAgent
from agentscope.message import Msg


class UserProxyAgent(UserAgent):
    """User proxy agent class"""

    def reply(
        self,
        x: Optional[Union[Msg, Sequence[Msg]]] = None,
        structured_output: Optional[Type[BaseModel]] = None,
    ) -> Msg:
        """
        Reply with `self.speak(x)`
        """
        if x is not None:
            self.speak(x)
        return super().reply(x, structured_output)

    def observe(self, x: Union[Msg, Sequence[Msg]]) -> None:
        """
        Observe with `self.speak(x)`
        """
        if x is not None:
            self.speak(x)  # type: ignore[arg-type]
            self.memory.add(x)
