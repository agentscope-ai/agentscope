# -*- coding: utf-8 -*-
"""An example workflow using AgentScope's ReAct agent to solve tasks.

This workflow is a demonstration of how to integrate the AgentScope
framework within the Trinity-RFT with minimal modifications.
"""

from typing import Dict, List, Optional, Union

import openai

from trinity.common.experience import Experience
from trinity.common.models.model import ModelWrapper
from trinity.common.workflows.workflow import WORKFLOWS, Task, Workflow
from agentscope.agent import ReActAgent
from agentscope.model import TrinityModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg

from .templates import TEMPLATE_MAP


@WORKFLOWS.register_module("react_agent")
class ReActWorkflow(Workflow):
    """An example workflow using AgentScope's ReAct agent to solve tasks."""

    def __init__(
        self,
        *,
        task: Task,
        model: ModelWrapper,
        auxiliary_models: Optional[List[openai.OpenAI]] = None,
    ) -> None:
        """Initialize the AgentScope ReAct workflow."""
        super().__init__(
            task=task,
            model=model,
            auxiliary_models=auxiliary_models,
        )

        task_type = task.workflow_args.get("type", "gsm8k")
        template = TEMPLATE_MAP.get(task_type, None)
        if template is None:
            raise ValueError(
                f"Unsupported task type {task_type} for AgentScope "
                "ReAct Agent, please add a template first.",
            )
        # extract the query and the answer from the task
        self.query = task.raw_task.get(task.format_args.prompt_key)
        self.answer = task.raw_task.get(task.format_args.response_key)
        self.reward_fn = template.reward_fn_cls(**task.reward_fn_args)
        self.response_structure = template.response_structure

        self.agent = ReActAgent(
            name="react_agent",
            sys_prompt=template.system_prompt,
            model=TrinityModel(
                openai_async_client=model.get_openai_async_client(),
                generate_kwargs={
                    "temperature": self.rollout_args.get("temperature", 1.0),
                    "max_tokens": self.rollout_args.get("max_tokens", 4096),
                },
            ),
            enable_meta_tool=True,
            formatter=OpenAIChatFormatter(),
        )

    async def run_async(self) -> List[Experience]:
        """Run the workflow asynchronously."""
        # Step 1: call the react agent to solve the task
        response = await self.agent.reply(
            msg=Msg("user", self.query, role="user"),
            structured_model=self.response_structure,
        )
        # Step 2: calculate the reward based on the response
        reward = await self.calculate_reward(response.metadata)
        # Step 3: construct experiences from the interaction
        # history and return them
        return self.construct_experiences(reward)

    async def calculate_reward(
        self,
        response: Dict,
    ) -> Union[float, Dict[str, float]]:
        """Calculate the reward for the workflow.

        Returns:
            Union[float, Dict[str, float]]: The reward value or a
                dictionary of reward value.
        """
        return self.reward_fn(response=response, truth=self.answer)

    def construct_experiences(
        self,
        reward: Union[float, Dict[str, float]],
    ) -> List[Experience]:
        """Construct experiences from the agent's interaction history.

        Args:
            reward (Union[float, Dict[str, float]]): The reward value
                to assign to each experience.

        Returns:
            List: A list of Experience objects.
        """
        exps = self.model.extract_experience_from_history()
        for exp in exps:
            exp.reward = (
                reward if isinstance(reward, float) else sum(reward.values())
            )
            exp.metrics = {
                "react_memory_length": len(self.agent.memory.content),
            }
            # record detailed reward if available
            if isinstance(reward, dict):
                exp.metrics.update(reward)
        return exps

    @property
    def asynchronous(self) -> bool:
        """AgentScope's ReAct agent only supports asynchronous calls,
        so we set this to True.
        """
        return True

    @property
    def repeatable(self) -> bool:
        """This workflow is not repeatable."""
        return False
