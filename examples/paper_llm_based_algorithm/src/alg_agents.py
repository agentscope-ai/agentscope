# -*- coding: utf-8 -*-
"""Agent and solver classes for LLM-based algorithms"""
import copy
from agentscope.agent import UserAgent
from agentscope.agent import DialogAgent
from agentscope.message import Msg
from src.utils import num_tokens_from_string

SYS_PROMPT_GENERIC = "You're a helpful assistant."
SYS_PROMPT_GPT = "You're a helpful assistant."
# "You are ChatGPT, a large language model trained by OpenAI."


class RequestAgent(UserAgent):
    """Request agent class

    Given a raw request for some algorithmic task, RequestAgent converts
    it to a prompt that is ready to be processed by a LLM solver.
    """

    def __init__(
        self,
        name: str = "User",
        require_url: bool = False,
    ) -> None:
        super().__init__(name=name, require_url=require_url)
        self.use_memory = False
        self.memory = None

    def reply(
        self,
        x: Msg = None,
        content: str = " ",
    ) -> Msg:
        """Return a message for content"""
        if x is not None:
            print(
                "=== WARNING: currently x is unused in RequestAgent.reply ===",
            )
        msg = Msg(
            name=self.name,
            role="user",
            content=content,
        )
        self.speak(msg)
        return msg


class ProblemSolver:
    """Problem solver class"""

    def __init__(self, config: dict) -> None:
        self.config = copy.deepcopy(config)
        self.cost_metrics = {
            "llm_calls": 0,
            "prefilling_length_total": 0,
            "decoding_length_total": 0,
            "prefilling_tokens_total": 0,
            "decoding_tokens_total": 0,
        }

    def spawn_request_agent(self) -> RequestAgent:
        """Spawn a RequestAgent"""
        return RequestAgent()

    def spawn_dialog_agent(self) -> DialogAgent:
        """Spawn a DialogAgent"""
        return DialogAgent(
            name="Assistant",
            sys_prompt=SYS_PROMPT_GENERIC,
            model_config_name=self.config["llm_model"],
            use_memory=False,
        )

    def reset_cost_metrics(self) -> None:
        """Reset cost metrics to zero"""
        for key in self.cost_metrics:
            self.cost_metrics[key] = 0

    def invoke_llm_call(
        self,
        x_request: Msg,
        dialog_agent: DialogAgent,
    ) -> Msg:
        """Invoke an LLM call, and update cost metrics accordingly"""

        # Call dialog_agent (or the LLM behind it)
        x = dialog_agent(x_request)

        # Update relevant self.cost_metrics
        self.cost_metrics["llm_calls"] += 1
        self.cost_metrics["prefilling_length_total"] += len(
            x_request.content,
        ) + len(dialog_agent.sys_prompt)
        self.cost_metrics["decoding_length_total"] += len(x.content)
        self.cost_metrics["prefilling_tokens_total"] += num_tokens_from_string(
            x_request.content,
        ) + num_tokens_from_string(dialog_agent.sys_prompt)
        self.cost_metrics["decoding_tokens_total"] += num_tokens_from_string(
            x.content,
        )

        return x
