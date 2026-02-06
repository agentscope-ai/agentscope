# -*- coding: utf-8 -*-
"""Wrapper modules for integrating AgentScope agents."""

import asyncio
from typing import Any
from dspy import Module, Prediction
import dspy
from dspy.predict.predict import Predict

from agentscope import logger
from agentscope.tuner._workflow import WorkflowOutput, PromptTuneWorkflowType


class _OptimizablePrompt(Predict):
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

    def forward(self, **kwargs: Any) -> Prediction:
        """Forward pass is not implemented.

        Raises:
            NotImplementedError: Always raised as this is a wrapper class.
        """
        raise NotImplementedError(
            "OptimizableAgent is a wrapper, not callable",
        )

    def sync_instruction(self) -> None:
        """Sync instruction from DSPy signature to internal state."""
        self.instructions = self.signature.instructions
        self._sys_prompt = self.instructions

    def get_current_prompt(self) -> str:
        """Get the current optimized system prompt."""
        return self._sys_prompt


class _WorkflowWrapperModule(Module):
    """A DSPy Module that wraps an AgentScope workflow for optimization.

    This module enables DSPy to optimize the system prompt by wrapping
    the workflow execution in a DSPy-compatible interface.

    Attributes:
        _workflow: The workflow function that takes task and system prompt.
        predictor: The OptimizableAgent wrapping the system prompt.
    """

    def __init__(
        self,
        workflow: PromptTuneWorkflowType,
        init_prompt: str,
    ):
        """Initialize the _WorkflowWrapperModule.

        Args:
            workflow: A workflow function that takes a task dict and system
                prompt string, returns an async WorkflowOutput.
            init_prompt: The initial system prompt to be optimized.
        """
        super().__init__()
        self._workflow = workflow
        self._init_prompt = init_prompt

        self.predictor = _OptimizablePrompt(self._init_prompt)

    def forward(self, inp: Any) -> Any:
        """Execute the workflow with the given input.

        Args:
            inp: The input data from DSPy.

        Returns:
            The response message from the workflow execution.
        """
        self.predictor.sync_instruction()
        current_prompt = self.predictor.get_current_prompt()

        async def _run_workflow() -> WorkflowOutput:
            return await self._workflow(task=inp, system_prompt=current_prompt)

        result = asyncio.run(_run_workflow())

        if result.reward:
            logger.warning(
                (
                    "reward in workflow output will be ignored,"
                    "use separate judge function"
                ),
            )

        return result.response
