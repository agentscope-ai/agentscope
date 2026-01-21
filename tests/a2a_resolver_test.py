# -*- coding: utf-8 -*-
"""The agent card resolver tests for A2A agents."""
import json
import os
from unittest import IsolatedAsyncioTestCase

from a2a.types import AgentCard, AgentCapabilities, AgentSkill

from agentscope.a2a import FileAgentCardResolver


class A2AAgentCardResolverTest(IsolatedAsyncioTestCase):
    """Test the A2A agent card resolver."""

    async def asyncSetUp(self) -> None:
        """Set up the test case."""
        self.agent_card = AgentCard(
            name="Friday",
            description="A simple ReAct agent that handles input queries",
            url="http://localhost:8000",
            version="1.0.0",
            capabilities=AgentCapabilities(
                push_notifications=False,
                state_transition_history=True,
                streaming=True,
            ),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=[
                AgentSkill(
                    name="execute_python_code",
                    id="execute_python_code",
                    description="Execute Python code snippets.",
                    tags=["code_execution"],
                ),
                AgentSkill(
                    name="execute_shell_command",
                    id="execute_shell_command",
                    description="Execute shell commands on the server.",
                    tags=["code_execution"],
                ),
                AgentSkill(
                    name="view_text_file",
                    id="view_text_file",
                    description="View the content of a text file on the "
                    "server.",
                    tags=["file_viewing"],
                ),
            ],
        )
        self.agent_card_path = "./test_agent_card.json"

    async def test_file_card_resolver(self) -> None:
        """Test the file agent card resolver."""
        try:
            # Save one agent card to a file
            json_dict = self.agent_card.model_dump()
            with open(self.agent_card_path, "w", encoding="utf-8") as file:
                json.dump(json_dict, file)

            agent_card = await FileAgentCardResolver(
                file_path=self.agent_card_path,
            ).get_agent_card()

            self.assertDictEqual(
                agent_card.model_dump(),
                self.agent_card.model_dump(),
            )

        finally:
            if os.path.exists(self.agent_card_path):
                os.remove(self.agent_card_path)
