# -*- coding: utf-8 -*-
"""The router agent which routes the questions to the corresponding agent."""
from typing import Optional, Union, Sequence

from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope.parsers import RegexTaggedContentParser


# Init a router agent
class RouterAgent(AgentBase):
    """
    The router agent who routes the questions to the corresponding agent.
    """

    def __init__(
        self,
        sys_prompt: str,
        model_config_name: str,
    ) -> None:
        """Init a router agent."""
        self.name = "Router"

        super().__init__(
            name=self.name,
            model_config_name=model_config_name,
        )

        self.sys_prompt = sys_prompt.format_map({"name": self.name})

        self.memory.add(Msg(self.name, self.sys_prompt, "system"))

        self.parser = RegexTaggedContentParser(
            format_instruction="""Respond with specific tags as outlined below:

- When routing questions to agent:
<thought>what you thought</thought>
<agent>the agent name</agent>

- When answering questions directly:
<thought>what you thought</thought>
<response>what you respond</response>
""",
            required_keys=["thought"],
        )

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        """The reply function."""
        self.memory.add(x)

        prompt = self.model.format(
            self.memory.get_memory(),
            Msg("system", self.parser.format_instruction, "system"),
        )

        response = self.model(prompt)

        # To be compatible with streaming mode
        self.speak(response.stream or response.text)

        # Parse the response by predefined parser
        parsed_dict = self.parser.parse(response).parsed

        msg = Msg(self.name, response.text, "assistant")

        # Assign the question to the corresponding agent in the metadata field
        if "agent" in parsed_dict:
            msg.metadata = parsed_dict["agent"]

        self.memory.add(msg)

        return msg
