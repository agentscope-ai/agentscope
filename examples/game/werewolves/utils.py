# -*- coding: utf-8 -*-
"""Utility functions for the werewolf game."""
from collections import defaultdict
from typing import Any

import numpy as np

from prompt import EnglishPrompts as Prompts

from agentscope.message import Msg
from agentscope.agent import ReActAgent, AgentBase

MAX_GAME_ROUND = 30
MAX_DISCUSSION_ROUND = 3


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


class Players:
    """Maintain the players' status."""

    def __init__(self) -> None:
        """Initialize the players."""
        # The mapping from player name to role
        self.name_to_role = {}
        self.role_to_names = defaultdict(list)
        self.name_to_agent = {}
        self.werewolves = []
        self.villagers = []
        self.seer = []
        self.hunter = []
        self.witch = []
        self.current_alive = []
        self.all_players = []

    def add_player(self, player: ReActAgent, role: str) -> None:
        """Add a player to the game.

        Args:
            player (`ReActAgent`):
                The player to be added.
            role (`str`):
                The role of the player.
        """
        self.name_to_role[player.name] = role
        self.name_to_agent[player.name] = player
        self.role_to_names[role].append(player.name)
        self.all_players.append(player)
        if role == "werewolf":
            self.werewolves.append(player)
        elif role == "villager":
            self.villagers.append(player)
        elif role == "seer":
            self.seer.append(player)
        elif role == "hunter":
            self.hunter.append(player)
        elif role == "witch":
            self.witch.append(player)
        else:
            raise ValueError(f"Unknown role: {role}")
        self.current_alive.append(player)

    def update_players(self, dead_players: list[ReActAgent]) -> None:
        """Update the current alive players.

        Args:
            dead_players (`list[ReActAgent]`):
                A list of dead players to be removed.
        """
        self.werewolves = [
            _ for _ in self.werewolves if _.name not in dead_players
        ]
        self.villagers = [
            _ for _ in self.villagers if _.name not in dead_players
        ]
        self.seer = [_ for _ in self.seer if _.name not in dead_players]
        self.hunter = [_ for _ in self.hunter if _.name not in dead_players]
        self.witch = [_ for _ in self.witch if _.name not in dead_players]
        self.current_alive = [
            _ for _ in self.current_alive if _.name not in dead_players
        ]

    def print_roles(self) -> None:
        """Print the roles of all players."""
        print("Roles:")
        for name, role in self.name_to_role.items():
            print(f" - {name}: {role}")

    def check_winning(self) -> str | None:
        """Check if the game is over and return the winning message."""

        # Prepare true roles string
        true_roles = (
            f'{names_to_str(self.role_to_names["werewolf"])} are werewolves, '
            f'{names_to_str(self.role_to_names["villager"])} are villagers, '
            f'{names_to_str(self.role_to_names["seer"])} is the seer, '
            f'{names_to_str(self.role_to_names["hunter"])} is the hunter, '
            f'and {names_to_str(self.role_to_names["witch"])} is the witch.'
        )

        if len(self.werewolves) * 2 >= len(self.current_alive):
            return Prompts.to_all_wolf_win.format(
                n_alive=len(self.current_alive),
                n_werewolves=len(self.werewolves),
                true_roles=true_roles,
            )
        if self.current_alive and not self.werewolves:
            return Prompts.to_all_village_win.format(
                true_roles=true_roles,
            )
        return None
