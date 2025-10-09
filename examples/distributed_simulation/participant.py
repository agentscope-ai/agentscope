# -*- coding: utf-8 -*-
"""A general dialog agent."""
import random
import time
import re
from typing import Optional, Union, Sequence

from loguru import logger

from agentscope.message import Msg
from agentscope.agent import AgentBase


class RandomParticipant(AgentBase):
    """A fake participant who generates number randomly."""

    def __init__(
        self,
        name: str,
        max_value: int = 100,
        sleep_time: float = 1.0,
    ) -> None:
        """Initialize the participant."""
        super().__init__(
            name=name,
        )
        self.max_value = max_value
        self.sleep_time = sleep_time

    def generate_random_response(self) -> str:
        """generate a random int"""
        time.sleep(self.sleep_time)
        return str(random.randint(0, self.max_value))

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        """Generate a random value"""
        # generate a response in content
        response = self.generate_random_response()
        msg = Msg(self.name, content=response, role="assistant")
        return msg


class LLMParticipant(AgentBase):
    """A participant agent who generates number using LLM."""

    def __init__(
        self,
        name: str,
        model_config_name: str,
        max_value: int = 100,
    ) -> None:
        """Initialize the participant."""
        super().__init__(
            name=name,
            model_config_name=model_config_name,
            use_memory=True,
        )
        self.max_value = max_value
        self.prompt = Msg(
            name="system",
            role="system",
            content="You are participating in a game where everyone "
            f"provides a number between 0 and {max_value}. The person "
            "closest to the average will win.",
        )

    def parse_value(self, txt: str) -> str:
        """Parse the number from the response."""
        numbers = re.findall(r"\d+", txt)
        if len(numbers) == 0:
            logger.warning(
                f"Fail to parse value from [{txt}], use "
                f"{self.max_value // 2} instead.",
            )
            return str(self.max_value // 2)
        else:
            return numbers[-1]

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        """Generate a value by LLM"""
        if self.memory:
            self.memory.add(x)

        # prepare prompt
        prompt = self.model.format(self.prompt, self.memory.get_memory())

        # call llm and generate response
        response = self.model(prompt).text

        response = self.parse_value(response)

        msg = Msg(self.name, response, role="assistant")

        # Record the message in memory
        if self.memory:
            self.memory.add(msg)

        return msg


class Moderator(AgentBase):
    """A Moderator to collect values from participants."""

    def __init__(
        self,
        name: str,
        part_configs: list[dict],
        agent_type: str = "random",
        max_value: int = 100,
        sleep_time: float = 1.0,
    ) -> None:
        super().__init__(name)
        self.max_value = max_value
        if agent_type == "llm":
            self.participants = [
                LLMParticipant(
                    name=config["name"],
                    model_config_name=config["model_config_name"],
                    max_value=max_value,
                ).to_dist(
                    host=config["host"],
                    port=config["port"],
                )
                for config in part_configs
            ]
        else:
            self.participants = [
                RandomParticipant(
                    name=config["name"],
                    max_value=max_value,
                    sleep_time=sleep_time,
                ).to_dist(
                    host=config["host"],
                    port=config["port"],
                )
                for config in part_configs
            ]

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        results = []
        msg = Msg(
            name="moderator",
            role="user",
            content=f"Now give a number between 0 and {self.max_value}.",
        )
        for p in self.participants:
            results.append(p(msg))
        summ = 0
        for r in results:
            try:
                summ += int(r.content)
            except Exception as e:
                print(e)
        return Msg(
            name=self.name,
            role="assistant",
            content={"sum": summ, "cnt": len(self.participants)},
        )
