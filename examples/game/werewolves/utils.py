# -*- coding: utf-8 -*-
"""Utility functions for the werewolf game."""
from typing import Any

import numpy as np

from prompt import Prompts

from agentscope.message import Msg
from agentscope.agent import ReActAgent, AgentBase

MAX_GAME_ROUND = 30
MAX_DISCUSSION_ROUND = 3

candidate_names = [
    "Batman",
    "Superman",
    "Joker",
    "Luoji",
    "Turing",
    "Einstein",
    "Newton",
    "Musk",
    "Jarvis",
    "Friday",
    "Spiderman",
    "Captain",
    "Harry",
    "Hermione",
    "Ron",
    "Gandalf",
    "Voldemort",
    "Frodo",
    "Aragorn",
    "Legolas",
    "Geralt",
    "Yennefer",
    "Triss",
    "Ciri",
    "Yeye",
    "Yaojing",
    "Dawa",
    "Erwa",
    "Sanwa",
    "Siwa",
    "Wuwa",
    "Wukong",
    "Bajie",
    "Shaseng",
    "Sanzang",
]


def get_player_name() -> str:
    """Generate player name."""
    return candidate_names.pop(np.random.randint(len(candidate_names)))


def check_winning(
    alive_agents: list,
    wolf_agents: list,
) -> str | None:
    """Check if the game is over and return the winning message."""
    if len(wolf_agents) * 2 >= len(alive_agents):
        return Prompts.to_all_wolf_win.format(
            n_werewolves=(
                f"{len(wolf_agents)}"
                + f"({names_to_str([_.name for _ in wolf_agents])})"
            ),
            n_villagers=len(alive_agents) - len(wolf_agents),
        )
    if alive_agents and not wolf_agents:
        return Prompts.to_all_village_win
    return None


def majority_vote(votes: list[str]) -> tuple:
    """Return the vote with the most counts."""
    result = max(set(votes), key=votes.count)
    names, counts = np.unique(votes, return_counts=True)
    conditions = ", ".join(
        [f"{name}: {count}" for name, count in zip(names, counts)],
    )
    return result, conditions


def names_to_str(agents: list[str] | list[ReActAgent]) -> str:
    """Return a string of agent names."""
    if not agents:
        return ""

    if len(agents) == 1:
        if isinstance(agents[0], ReActAgent):
            return agents[0].name
        return agents[0]

    names = []
    for agent in agents:
        if isinstance(agent, ReActAgent):
            names.append(agent.name)
        else:
            names.append(agent)
    return ", ".join([*names[:-1], "and " + names[-1]])


class EchoAgent(AgentBase):
    """Echo agent that repeats the input message."""

    def __init__(self) -> None:
        super().__init__()
        self.name = "Moderator"

    async def reply(self, content: str) -> Msg:
        """Repeat the input content with its name and role."""
        msg = Msg(
            self.name,
            content,
            role="assistant",
        )
        await self.print(msg)
        return msg

    async def handle_interrupt(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """Handle interrupt."""

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Observe the user's message."""
