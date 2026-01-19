"""Wrapper modules for integrating AgentScope agents."""

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
    """A DSPy Predict wrapper that makes a ReActAgent's prompt optimizable.

    This class bridges AgentScope's ReActAgent with DSPy's optimization
    framework by exposing the agent's system prompt as a DSPy signature.

    Attributes:
        _agent: The wrapped ReActAgent instance.
    """

    def __init__(self, agent: ReActAgent):
        """Initialize the OptimizableAgent.

        Args:
            agent: The ReActAgent to wrap for optimization.
        """
        super().__init__("input -> output")
        self.signature = dspy.make_signature("input -> output")
        self.instructions = self.signature.instructions
        self.demos = []

        self._agent = agent
        # sync init instruction from agent to dspy signature
        self.instructions = self._agent._sys_prompt
        self.signature.instructions = self.instructions

    def forward(self, **kwargs):
        """Forward pass is not implemented.

        Raises:
            NotImplementedError: Always raised as this is a wrapper class.
        """
        raise NotImplementedError(
            "OptimizableAgent is a wrapper, not callable")

    def _sync_instruction_i2a(self):
        """Sync instruction from DSPy signature to the wrapped agent."""
        self.instructions = self.signature.instructions
        self._agent._sys_prompt = self.instructions


class WorkflowWrapperModule(Module):
    """A DSPy Module that wraps an AgentScope workflow for optimization.

    This module enables DSPy to optimize the system prompt of a ReActAgent
    by wrapping the workflow execution in a DSPy-compatible interface.

    Attributes:
        _workflow: The workflow factory function.
        _agent: The ReActAgent being optimized.
        predictor: The OptimizableAgent wrapping the agent's prompt.
    """

    def __init__(self, workflow: Callable[[ReActAgent], WorkflowType], init_agent: ReActAgent):
        """Initialize the WorkflowWrapperModule.

        Args:
            workflow: A factory function that takes a ReActAgent and returns
                an async workflow function.
            init_agent: The initial ReActAgent to be optimized.
        """
        super().__init__()
        self._workflow = workflow
        self._agent = init_agent

        self.predictor = OptimizableAgent(self._agent)

    def _set_chatmodel(self, model: ChatModelBase, auxiliary_models: dict[str, ChatModelBase]):
        """Set the chat models for workflow execution.

        Args:
            model: The primary chat model.
            auxiliary_models: Dictionary of additional chat models.
        """
        self._model = model
        self._auxiliary_models = auxiliary_models

    def forward(self, inp):
        """Execute the workflow with the given input.

        Args:
            inp: The input data from DSPy.

        Returns:
            The response message from the workflow execution.
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