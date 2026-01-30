# -*- coding: utf-8 -*-
""""""
from typing import Type

from pydantic import BaseModel

from ._react_agent_base import ReActAgentBase
from ..memory import MemoryBase, InMemoryMemory
from ..message import Msg
from ..tool import Toolkit


class Action(ReActAgentBase):
    """The action class for the ReAct agent plus."""
    type: str  # 'reasoning' or 'acting'


class AgentIntentionBase:
    """The base class for agent intentions."""

class ExitReplyIntention(AgentIntentionBase):
    """The intention for exiting the reply loop."""

    def setup(
        self,
    ) -> None:
        """Set up the intention before execution."""


    def decide(
        self,
        reply_input: dict,
        current_iter: int,
        memory: MemoryBase,
        toolkit: Toolkit,
    ) -> Action | None:


class RAGIntention(AgentIntentionBase):

    def decide(
        self,

    ):



class ReActAgentPlus(ReActAgentBase):
    """The ReAct agent plus class, which is an enhanced version of ReActAgent
    implementation, which is driven by AgentTasks and more flexible.
    """

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        memory: MemoryBase | None = None,
        toolkit: Toolkit | None = None,
        intentions: list[AgentIntentionBase] | None = None,
    ) -> None:
        super().__init__()

        # The attributes that truly belong to the agent itself
        self.name = name
        self._sys_prompt = sys_prompt
        self.memory = memory or InMemoryMemory()
        self.toolkit = toolkit or Toolkit()

        # The intentions that drive the agent's behavior
        self.intentions = intentions or [

        ]

    def reply(
        self,
        msg: Msg | list[Msg] | None,
        structured_model: Type[BaseModel] | None = None,
        max_iters: int = 10,
    ) -> Msg:
        """

        """

        current_iter = 0

        while True:
            intention, instructions, tool_choice = self._decide_intention(
                msg,
                structured_model,
                max_iters,
                current_iter,
            )

    def _decide_intention(self, msg: Msg | list[Msg] | None, structured_model, current_iter: int, max_iters: int) -> None:
        """"""
        # From the top to the bottom, check which intention can be executed

        if current_iter == 0:
            # 判断进入点
            if msg.get_content_blocks('tool_use'):
                # 如果有工具调用，进入 Acting 阶段
                return 'acting', None, None





