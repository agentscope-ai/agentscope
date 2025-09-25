# -*- coding: utf-8 -*-
"""A Gomoku agent that can play the game with another agent."""

from typing import Optional, Union, Sequence

import json

from agentscope.message import Msg
from agentscope.agents import AgentBase
from agentscope.model import ModelResponse

HINT_PROMPT = """
You should respond in the following format, which can be loaded by json.loads in Python:
{{
    "thought": "analyze the present situation, and what move you should make",
    "move": [row index, column index]
}}
"""  # noqa


def parse_func(response: ModelResponse) -> ModelResponse:
    """Parse the response from the model into a dict with "move" and "thought"
    keys."""
    res_dict = json.loads(response.text)
    if "move" in res_dict and "thought" in res_dict:
        return ModelResponse(raw=res_dict)
    else:
        raise ValueError(
            f"Invalid response format in parse_func "
            f"with response: {response.text}",
        )


class GomokuAgent(AgentBase):
    """A Gomoku agent that can play the game with another agent."""

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model_config_name: str,
    ) -> None:
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model_config_name=model_config_name,
        )

        self.memory.add(Msg("system", sys_prompt, role="system"))

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        if self.memory:
            self.memory.add(x)

        msg_hint = Msg("system", HINT_PROMPT, role="system")

        prompt = self.model.format(
            self.memory.get_memory(),
            msg_hint,
        )

        response = self.model(
            prompt,
            parse_func=parse_func,
            max_retries=3,
        ).raw

        # For better presentation, we print the response proceeded by
        # json.dumps, this msg won't be recorded in memory
        self.speak(
            Msg(
                self.name,
                json.dumps(response, indent=4, ensure_ascii=False),
                role="assistant",
            ),
        )

        if self.memory:
            self.memory.add(Msg(self.name, response, role="assistant"))

        # Hide thought from the response
        return Msg(self.name, response["move"], role="assistant")
