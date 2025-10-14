# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""The main entry point for the werewolf game."""
import asyncio
import os

from game import werewolves_game

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.model import DashScopeChatModel
from agentscope.session import JSONSession


def get_official_agents(name: str) -> ReActAgent:
    """Get the official werewolves game agents."""
    agent = ReActAgent(
        name=name,
        sys_prompt=f"""You're a werewolf game player named {name}.

# YOUR TARGET
Your target is to win the game with your teammates as much as possible.

# GAME RULES
- In werewolf game, players are divided into three werewolves, three villagers, one seer, one hunter and one witch.
    - Werewolves: kill one player each night, and must hide identity during the day.
    - Villagers: ordinary players without special abilities, try to identify and eliminate werewolves.
        - Seer: A special villager who can check one player's identity each night.
        - Witch: A special villager with two one-time-use potions: a healing potion to save a player from being killed at night, and a poison to eliminate one player at night.
        - Hunter: A special villager who can take one player down with them when they are eliminated.
- The game alternates between night and day phases until one side wins:
    - Night Phase
        - Werewolves choose one victim
        - Seer checks one player's identity
        - Witch decides whether to use potions
        - Moderator announces who died during the night
    - Day Phase
        - All players discuss and vote to eliminate one suspected player

# GAME GUIDANCE
- Try your best to win the game with your teammates, tricks, lies, and deception are all allowed, e.g. pretending to be a different role.
- During discussion, don't be political, be direct and to the point.
- The day phase voting provides important clues. For example, the werewolves may vote together, attack the seer, etc.
## GAME GUIDANCE FOR WEREWOLF
- Seer is your greatest threat, who can check one player's identity each night. Analyze players' speeches, find out the seer and eliminate him/her will greatly increase your chances of winning.
- In the first night, making random choices is common for werewolves since no information is available.
- Pretending to be other roles (seer, witch or villager) is a common strategy to hide your identity and mislead other villagers in the day phase.
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, if the dead player is hunter, etc. Use this information to adjust your strategy.
## GAME GUIDANCE FOR SEER
- Seer is very important to villagers, exposing yourself too early may lead to being targeted by werewolves.
- Your ability to check one player's identity is crucial.
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, if the dead player is hunter, etc. Use this information to adjust your strategy.
## GAME GUIDANCE FOR WITCH
- Witch has two powerful potions, use them wisely to protect key villagers or eliminate suspected werewolves.
- The outcome of the night phase provides important clues. For example, if the dead player is hunter, etc. Use this information to adjust your strategy.
## GAME GUIDANCE FOR HUNTER
- Using your ability in day phase will expose your role (since only hunter can take one player down)
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, etc. Use this information to adjust your strategy.
## GAME GUIDANCE FOR VILLAGER
- Protecting special villagers, especially the seer, is crucial for your team's success.
- Werewolves may pretend to be the seer. Be cautious and don't trust anyone easily.
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, if the dead player is hunter, etc. Use this information to adjust your strategy.

# NOTE
- [IMPORTANT] DO NOT make up any information that is not provided by the moderator or other players.
- This is a TEXT-based game, so DO NOT use or make up any non-textual information.
- Always critically reflect on whether your evidence exist, and avoid making assumptions.
- Your response should be specific and concise, provide clear reason and avoid unnecessary elaboration.
- Generate your one-line response by using the `generate_response` function.
- Don't repeat the others' speeches.""",
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="qwen3-max",
        ),
        formatter=DashScopeMultiAgentFormatter(),
    )
    return agent


async def main() -> None:
    """The main entry point for the werewolf game."""

    # Uncomment the following lines if you want to use Agentscope Studio
    # to visualize the game process.
    # import agentscope
    # agentscope.init(
    #     studio_url="http://localhost:3000",
    #     project="werewolf_game",
    # )

    # Prepare 9 players, you can change their names here
    players = [get_official_agents(f"Player{_ + 1}") for _ in range(9)]

    # Note: You can replace your own agents here, or use all your own agents

    # Load states from a previous checkpoint
    session = JSONSession(save_dir="./checkpoints")
    await session.load_session_state(
        session_id="players_checkpoint",
        **{player.name: player for player in players},
    )

    await werewolves_game(players)

    # Save the states to a checkpoint
    await session.save_session_state(
        session_id="players_checkpoint",
        **{player.name: player for player in players},
    )


asyncio.run(main())
