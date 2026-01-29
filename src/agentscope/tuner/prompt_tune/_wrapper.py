# -*- coding: utf-8 -*-
"""Wrapper modules for integrating AgentScope agents."""

import asyncio
from typing import Callable
from dspy import Module
import dspy
from agentscope import logger
from agentscope.model._model_base import ChatModelBase
from agentscope.tuner._workflow import WorkflowType

from dspy.predict.predict import Predict


class OptimizablePrompt(Predict):
    """A DSPy Predict wrapper that makes a system prompt optimizable.

    This class bridges AgentScope's ReActAgent with DSPy's optimization
    framework by exposing the system prompt as a DSPy signature.

    Attributes:
        _sys_prompt: The current system prompt being optimized.
    """

    def __init__(self, init_prompt: str):
        """Initialize the OptimizableAgent.

        Args:
            init_prompt: The initial system prompt to optimize.
        """
        super().__init__("input -> output")
        self.signature = dspy.make_signature("input -> output")
        self.instructions = self.signature.instructions
        self.demos = []

        self._sys_prompt = init_prompt
        self.instructions = self._sys_prompt
        self.signature.instructions = self.instructions

    def forward(self, **kwargs):
        """Forward pass is not implemented.

        Raises:
            NotImplementedError: Always raised as this is a wrapper class.
        """
        raise NotImplementedError(
            "OptimizableAgent is a wrapper, not callable",
        )

    def _sync_instruction(self) -> None:
        """Sync instruction from DSPy signature to internal state."""
        self.instructions = self.signature.instructions
        self._sys_prompt = self.instructions

    def get_current_prompt(self) -> str:
        """Get the current optimized system prompt."""
        return self._sys_prompt


class WorkflowWrapperModule(Module):
    """A DSPy Module that wraps an AgentScope workflow for optimization.

    This module enables DSPy to optimize the system prompt by wrapping
    the workflow execution in a DSPy-compatible interface.

    Attributes:
        _workflow: The workflow factory function that takes a system prompt.
        predictor: The OptimizableAgent wrapping the system prompt.
    """

    def __init__(
        self,
        workflow: Callable[[str], WorkflowType],
        init_prompt: str,
    ):
        """Initialize the WorkflowWrapperModule.

        Args:
            workflow: A factory function that takes a system prompt and returns
                an async workflow function.
            init_prompt: The initial system prompt to be optimized.
        """
        super().__init__()
        self._workflow = workflow
        self._init_prompt = init_prompt

        self.predictor = OptimizablePrompt(self._init_prompt)

    def _set_chatmodel(
        self,
        model: ChatModelBase,
        auxiliary_models: dict[str, ChatModelBase],
    ):
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
        self.predictor._sync_instruction()
        current_prompt = self.predictor.get_current_prompt()

        async def _workflow():
            return await self._workflow(current_prompt)(
                inp,
                self._model,
                self._auxiliary_models,
            )

        result = asyncio.run(_workflow())

        if result.reward:
            logger.warning(
                (
                    "reward in workflow output will be ignored,"
                    "use separate judge function"
                ),
            )

        return result.response
