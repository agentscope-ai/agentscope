# -*- coding: utf-8 -*-
"""The Abtest module to show how different system prompt performs"""
from typing import List, Optional, Union, Sequence
from loguru import logger

from agentscope.manager import ModelManager
from agentscope.message import Msg
from agentscope.agent import UserAgent, AgentBase


class _SystemPromptTestAgent(AgentBase):
    """An agent class used to test the given system prompt."""

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model_config_name: str,
    ) -> None:
        """Init the agent with the given system prompt, model config name,
        and name.

        Args:
            name (`str`):
                The name of the agent.
            sys_prompt (`str`):
                The system prompt to be tested.
            model_config_name (`str`):
                The model config name to be used.
        """
        super().__init__(name, sys_prompt, model_config_name)
        self.display = False

        self.memory.add(Msg("system", self.sys_prompt, "system"))

    def disable_display(self) -> None:
        """Disable the display of the output message."""
        self.display = False

    def enable_display(self) -> None:
        """Enable the display of the output message."""
        self.display = True

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        """Reply the message with the given system prompt."""
        self.memory.add(x)

        prompt = self.model.format(self.memory.get_memory())

        res = self.model(prompt)

        msg = Msg(self.name, res.text, "assistant")

        if self.display:
            self.speak(msg)

        self.memory.add(msg)

        return msg


class SystemPromptComparer:
    """The Abtest module to compare how different system prompts perform with
    different queries or in a multi-turn dialog."""

    def __init__(
        self,
        model_config_name: str,
        compared_system_prompts: List[str],
    ) -> None:
        """Init the Abtest module, the model config name, user prompt,
        and a list of prompt optimization methods or prompts are required.

        Args:
            model_config_name (`str`):
                The model config for the model to be used to generate
                and compare prompts.
            compared_system_prompts (`List[str]`):
                A list of system prompts to be compared in the abtest.
        """

        model_manager = ModelManager.get_instance()
        self.model_config_name = model_config_name
        self.model = model_manager.get_model_by_config_name(model_config_name)
        self.compared_system_prompts = compared_system_prompts

        # TODO: use distributed agent to accelerate the process
        self.agents = [
            _SystemPromptTestAgent(
                f"assistant-{index}",
                sys_prompt=sys_prompt,
                model_config_name=model_config_name,
            )
            for index, sys_prompt in enumerate(self.compared_system_prompts)
        ]

    def _compare_with_query(self, query: str) -> dict:
        """Infer the query with the given system prompt."""
        msg_query = Msg("user", query, "user")
        msgs_result = [agent(msg_query) for agent in self.agents]

        results = []
        for system_prompt, response_msg in zip(
            self.compared_system_prompts,
            msgs_result,
        ):
            results.append(
                {
                    "system_prompt": system_prompt,
                    "response": response_msg.content,
                },
            )
        return {
            "query": query,
            "results": results,
        }

    def _set_display_status(self, status: bool) -> None:
        """Set the display status of all agent."""
        for agent in self.agents:
            if status:
                agent.enable_display()
            else:
                agent.disable_display()

    def compare_with_queries(self, queries: List[str]) -> List[dict]:
        """Compare different system prompts a list of input queries.

        Args:
            queries (`List[str]`):
                A list of input queries that will be used to compare different
                system prompts.

        Returns:
            `List[dict]`: A list of responses of the queries with different
            system prompts.
        """

        self._set_display_status(False)

        query_results = []
        for index, query in enumerate(queries):
            # Print the query
            logger.info(f"## Query {index}:\n{query}")
            res = self._compare_with_query(query)

            for index_prompt, _ in enumerate(res["results"]):
                logger.info(
                    f"### System Prompt {index_prompt}\n"
                    f"```\n"
                    f"{_['system_prompt']}\n"
                    f"```\n"
                    f"\n"
                    f"### Response\n"
                    f"{_['response']}\n",
                )

            query_results.append(res)

        self._clear_memories()

        return query_results

    def compare_in_dialog(self) -> List[dict]:
        """Compare how different system prompts perform in a multi-turn dialog.
        Users can press `exit` to exit the dialog.

        Returns:
            `List[dict]`: A list of dictionaries, which contains the tested
            system prompts and the dialog history.
        """

        for agent in self.agents:
            Msg(
                agent.name,
                f"My system prompt: ```{agent.sys_prompt}```",
                "assistant",
                echo=True,
            )

        print("\n", " Start the dialog, input `exit` to exit ".center(80, "#"))

        self._set_display_status(True)

        user_agent = UserAgent()

        x = None
        while x is None or x.content != "exit":
            for agent in self.agents:
                agent(x)
            x = user_agent()

        # Get the dialog history
        results = [
            {
                "system_prompt": _.sys_prompt,
                "dialogue_history": _.memory.get_memory(),
            }
            for _ in self.agents
        ]

        # Clean the memory
        self._clear_memories()

        return results

    def _clear_memories(self) -> None:
        """Clear the memory of all agent."""
        for agent in self.agents:
            agent.memory.clear()
