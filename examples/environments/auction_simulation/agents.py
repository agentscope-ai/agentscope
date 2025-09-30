# -*- coding: utf-8 -*-
"""The agent used to simulate an auction."""
import random
import re
import time
from typing import Optional, Sequence, Union

from env import Item

from loguru import logger
from agentscope.agent import AgentBase
from agentscope.message import Msg


class RandomBidder(AgentBase):
    """A fake bidder agent who bids randomly."""

    def __init__(
        self,
        name: str,
        money: int = 100,
        not_bid_ratio: float = 0.5,
        sleep_time: float = 1.0,
    ) -> None:
        """Initialize the bidder agent."""
        super().__init__(name=name)
        self.money = money
        self.not_bid_ratio = not_bid_ratio
        self.sleep_time = sleep_time

    def generate_random_response(self, start: int = 0) -> Optional[int]:
        """Generate a random bid or not to bid."""
        time.sleep(random.random() * self.sleep_time)
        if random.random() < self.not_bid_ratio:
            return None
        return random.randint(start, self.money)

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        """Generate a random value"""
        item = Item.from_dict(x.content["item"])
        # generate a random bid or not to bid
        response = self.generate_random_response(item.opening_price)
        if response is None:
            self.speak(
                Msg(
                    self.name,
                    content=f"Not bid for {item.name}",
                    role="assistant",
                ),
            )
            return Msg(self.name, content=None, role="assistant")
        else:
            self.speak(
                Msg(
                    self.name,
                    content=f"Bid {response} for {item.name}",
                    role="assistant",
                ),
            )
        msg = Msg(self.name, content=response, role="assistant")
        return msg


class Bidder(AgentBase):
    """The bidder agent."""

    def __init__(
        self,
        name: str,
        model_config_name: str,
        money: int = 100,
    ) -> None:
        """Initialize the bidder agent."""
        super().__init__(
            name=name,
            model_config_name=model_config_name,
            use_memory=True,
        )
        self.money = money
        self.prompt = Msg(
            name="system",
            role="system",
            content="You are a bidder. You will be given an item. "
            f"You have {self.money} money. "
            "Please consider whether to bid for the item. "
            "If you want to bid, please provide the bid value "
            "(an integer between 1 and your money). "
            "If you don't want to bid, please provide 0.",
        )

    def parse_value(self, txt: str) -> Optional[int]:
        """Parse the bid from the response."""
        numbers = re.findall(r"\d+", txt)
        if len(numbers) == 0:
            logger.warning(
                f"Fail to parse value from [{txt}], use not bidding instead.",
            )
            return None
        elif int(numbers[-1]) > self.money:
            logger.warning(
                f"Try to bid more than {self.money}, "
                f"use {self.money} instead.",
            )
            return self.money
        else:
            return int(numbers[-1]) if numbers[-1] != "0" else None

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        """Generate a value by LLM"""

        if self.memory:
            self.memory.add(x)

        item = Item.from_dict(x.content["item"])
        bidder_name = x.content.get("bidder_name", None)
        prev_bid = x.content.get("bid", None)
        content = (
            f"The item is {item.name} and "
            f"the opening price is {item.opening_price}."
        )
        if bidder_name and prev_bid:
            content += f"\n{bidder_name} bid {prev_bid} for the item."
        bid_info = Msg("assistant", content=content, role="assistant")

        # prepare prompt
        prompt = self.model.format(
            self.prompt,
            self.memory.get_memory(),
            bid_info,
        )

        # call llm and generate response
        response = self.model(prompt).text
        bid = self.parse_value(response)
        msg = Msg(self.name, bid, role="assistant")
        if response is None:
            self.speak(
                Msg(
                    self.name,
                    content=f"Not bid for {item.name}",
                    role="assistant",
                ),
            )
        else:
            self.speak(
                Msg(
                    self.name,
                    content=f"Bid {response} for {item.name}",
                    role="assistant",
                ),
            )
        # Record the message in memory
        if self.memory:
            self.memory.add(msg)

        return msg
