
import asyncio
import os
import random
from types import SimpleNamespace
from typing import Callable
from datasets import load_dataset
from dspy import Module
import dspy
from dspy.datasets import DataLoader
from agentscope import logger
from agentscope.formatter._openai_formatter import OpenAIChatFormatter
from agentscope.message._message_base import Msg
from agentscope.model._dashscope_model import DashScopeChatModel
from agentscope.model._model_base import ChatModelBase
from agentscope.tuner._workflow import WorkflowOutput, WorkflowType
from agentscope.agent import ReActAgent

from dspy.predict.predict import Predict


class OptimizableAgent(Predict):
    def __init__(self, agent: ReActAgent):
        super().__init__("input -> output")
        self.signature = dspy.make_signature("input -> output")
        self.instructions = self.signature.instructions
        self.demos = []

        self._agent = agent
        # sync init instruction from agent to dspy signature
        self.instructions = self._agent._sys_prompt
        self.signature.instructions = self.instructions

    def forward(self, **kwargs):
        raise NotImplementedError(
            "OptimizableAgent is a wrapper, not callable")

    def _sync_instruction_i2a(self):
        """sync instruction from dspy signature to agent"""
        self.instructions = self.signature.instructions
        self._agent._sys_prompt = self.instructions


class WorkflowWrapperModule(Module):
    def __init__(self, workflow: Callable[[ReActAgent], WorkflowType], init_agent: ReActAgent):
        super().__init__()
        self._workflow = workflow
        self._agent = init_agent

        self.predictor = OptimizableAgent(self._agent)

    def _set_chatmodel(self, model: ChatModelBase, auxiliary_models: dict[str, ChatModelBase]):
        self._model = model
        self._auxiliary_models = auxiliary_models

    def forward(self, inp):
        """
        Args:
            inputs (Dict): The inputs from dspy, including data only.
        """

        self.predictor._sync_instruction_i2a()

        async def _workflow():
            # dspy deepcopy modules during optimization,
            # the new agent must be injected in real-time.
            return await self._workflow(self._agent)(inp, self._model, self._auxiliary_models)

        result = asyncio.run(_workflow())

        if result.reward:
            logger.warning(
                "reward in workflow output will be ignored, use separate judge function")

        return result.response