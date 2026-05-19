# -*- coding: utf-8 -*-
"""Branching pipeline classes for conditional routing and parallel execution.

This module provides three types of branching pipelines:
- IfElsePipeline: Route messages based on a boolean condition
- SwitchPipeline: Route messages based on a key lookup
- ParallelBranchPipeline: Send messages to multiple agents concurrently

Customer Support Workflow Example:
    .. code-block:: python

        # Agents definition
        classifier_agent = IntentClassifierAgent()
        billing_agent = BillingSupportAgent()
        technical_agent = TechnicalSupportAgent()
        general_agent = GeneralSupportAgent()
        refund_agent = RefundAgent()
        upgrade_agent = UpgradeAgent()
        confirmation_agent = ConfirmationAgent()
        escalation_agent = EscalationAgent()

        # Step 1: Classify intent and route to specialized agents
        # Using SwitchPipeline for intent-based routing
        intent_router = SwitchPipeline(
            key_fn=lambda msg: msg.metadata.get("intent", "general"),
            cases={
                "billing": SequentialPipeline([
                    # If billing issue, check if it's a refund request
                    IfElsePipeline(
                        condition=lambda msg: "refund" in msg.content.lower(),
                        true_agent=refund_agent,
                        false_agent=billing_agent,
                    ),
                    confirmation_agent,
                ]),
                "technical": SequentialPipeline([
                    technical_agent,
                    # Parallel: send to both QA and documentation teams
                    ParallelBranchPipeline(confirmation_agent, escalation_agent),
                ]),
                "upgrade": upgrade_agent,
            },
            default=general_agent,
        )

        # Full workflow: classify then route
        async def customer_support_workflow(user_msg: Msg) -> Msg:
            # First, classify the intent
            classified_msg = await classifier_agent(user_msg)
            # Then route based on intent
            result = await intent_router(classified_msg)
            return result

    Visual Layout:
        User Message
            │
            ▼
        ┌─────────────────────────────────────────┐
        │         IntentClassifierAgent           │
        │  (determines: billing/technical/upgrade)│
        └─────────────────────────────────────────┘
            │
            ▼
        ┌─────────────────────────────────────────┐
        │         SwitchPipeline (intent)         │
        ├─────────────┬─────────────┬─────────────┤
        │   billing   │  technical  │   upgrade   │
        │             │             │   (default) │
        └──────┬──────┴──────┬──────┴──────┬──────┘
               │             │             │
               ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────────┐
        │Sequential│  │Sequential│  │ UpgradeAgent │
        │ Pipeline │  │ Pipeline │  └──────────────┘
        └────┬─────┘  └────┬─────┘
             │             │
             ▼             ▼
        ┌──────────┐  ┌─────────────────┐
        │IfElse    │  │TechnicalAgent   │
        │Pipeline  │  └────────┬────────┘
        └────┬─────┘           │
             │                 ▼
        ┌────┴────┐  ┌─────────────────────┐
        │Refund   │  │ParallelBranchPipeline│
        │Agent    │  │(Confirm + Escalation)│
        │or Billing│  └─────────────────────┘
        │Agent    │
        └────┬────┘
             │
             ▼
        ┌────────────┐
        │Confirmation│
        │Agent       │
        └────────────┘
"""
import asyncio
import inspect
from copy import deepcopy
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union

from ..agent import AgentBase
from ..message import Msg


class IfElsePipeline:
    """A conditional pipeline that routes messages between two agents based on
    a condition function.

    Example:
        .. code-block:: python

            def is_question(msg: Msg) -> bool:
                return "?" in msg.content

            agent_a = ReActAgent(...)  # Handles questions
            agent_b = ReActAgent(...)  # Handles statements

            pipeline = IfElsePipeline(is_question, agent_a, agent_b)
            result = await pipeline(msg)
    """

    def __init__(
        self,
        condition: Callable[[Msg], Union[bool, Coroutine[Any, Any, bool]]],
        true_agent: AgentBase,
        false_agent: AgentBase,
    ) -> None:
        """Initialize an IfElsePipeline.

        Args:
            condition (`Callable[[Msg], Union[bool, Coroutine[Any, Any, bool]]]`):
                A function that takes a Msg and returns a bool. Can be sync
                or async.
            true_agent (`AgentBase`):
                The agent to execute when condition returns True.
            false_agent (`AgentBase`):
                The agent to execute when condition returns False.
        """
        self.condition = condition
        self.true_agent = true_agent
        self.false_agent = false_agent

    async def __call__(
        self,
        msg: Msg | list[Msg] | None = None,
    ) -> Msg | list[Msg] | None:
        """Execute the if-else pipeline.

        Args:
            msg (`Msg | list[Msg] | None`, defaults to `None`):
                The input message to evaluate and route.

        Returns:
            `Msg | list[Msg] | None`:
                The result from either true_agent or false_agent.
        """
        if msg is None:
            return None

        if inspect.iscoroutinefunction(self.condition):
            condition_result = await self.condition(msg)
        else:
            condition_result = self.condition(msg)

        if condition_result:
            return await self.true_agent(msg)
        else:
            return await self.false_agent(msg)


class SwitchPipeline:
    """A conditional pipeline that routes messages to one of several agents
    based on a key function.

    Example:
        .. code-block:: python

            def get_topic(msg: Msg) -> str:
                if "weather" in msg.content.lower():
                    return "weather"
                elif "news" in msg.content.lower():
                    return "news"
                return "default"

            weather_agent = ReActAgent(...)
            news_agent = ReActAgent(...)
            general_agent = ReActAgent(...)

            pipeline = SwitchPipeline(
                key_fn=get_topic,
                cases={
                    "weather": weather_agent,
                    "news": news_agent,
                },
                default=general_agent
            )
            result = await pipeline(msg)
    """

    def __init__(
        self,
        key_fn: Callable[[Msg], Union[str, Coroutine[Any, Any, str]]],
        cases: Dict[str, AgentBase],
        default: Optional[AgentBase] = None,
    ) -> None:
        """Initialize a SwitchPipeline.

        Args:
            key_fn (`Callable[[Msg], Union[str, Coroutine[Any, Any, str]]]`):
                A function that takes a Msg and returns a string key.
                Can be sync or async.
            cases (`Dict[str, AgentBase]`):
                A dictionary mapping keys to agents.
            default (`Optional[AgentBase]`, defaults to `None`):
                The agent to use when the key doesn't match any case.
                If None and key doesn't match, KeyError is raised.
        """
        self.key_fn = key_fn
        self.cases = cases
        self.default = default

    async def __call__(
        self,
        msg: Msg | list[Msg] | None = None,
    ) -> Msg | list[Msg] | None:
        """Execute the switch pipeline.

        Args:
            msg (`Msg | list[Msg] | None`, defaults to `None`):
                The input message to evaluate and route.

        Returns:
            `Msg | list[Msg] | None`:
                The result from the selected agent.

        Raises:
            KeyError: If key doesn't match any case and no default is provided.
        """
        if msg is None:
            return None

        if inspect.iscoroutinefunction(self.key_fn):
            key = await self.key_fn(msg)
        else:
            key = self.key_fn(msg)

        if key in self.cases:
            return await self.cases[key](msg)
        elif self.default is not None:
            return await self.default(msg)
        else:
            raise KeyError(f"No case found for key: {key} and no default agent provided")


class ParallelBranchPipeline:
    """A pipeline that sends the same message to multiple agents concurrently
    and collects all their responses.

    Example:
        .. code-block:: python

            agent1 = ReActAgent(...)
            agent2 = ReActAgent(...)
            agent3 = ReActAgent(...)

            pipeline = ParallelBranchPipeline(agent1, agent2, agent3)
            results = await pipeline(msg)
            # results is a list of Msg from each agent
    """

    def __init__(
        self,
        *agents: AgentBase,
    ) -> None:
        """Initialize a ParallelBranchPipeline.

        Args:
            *agents (`AgentBase`):
                Variable number of agents to execute in parallel.
                An empty list is allowed and will result in an empty
                list being returned when called.
        """
        self.agents = list(agents)

    async def __call__(
        self,
        msg: Msg | list[Msg] | None = None,
        **kwargs: Any,
    ) -> List[Union[Msg, list[Msg], None, Exception]]:
        """Execute the parallel branch pipeline.

        Args:
            msg (`Msg | list[Msg] | None`, defaults to `None`):
                The input message to send to all agents.
            **kwargs (`Any`):
                Additional keyword arguments passed to each agent.

        Returns:
            `List[Union[Msg, list[Msg], None, Exception]]`:
                A list of results from each agent. If an agent raises an
                exception, the exception will be in the list instead of
                a result (using return_exceptions=True). If the agent list
                is empty, an empty list `[]` is returned.
        """
        tasks = [
            asyncio.create_task(agent(deepcopy(msg), **kwargs))
            for agent in self.agents
        ]

        return await asyncio.gather(*tasks, return_exceptions=True)
