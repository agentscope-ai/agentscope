# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches, too-many-statements, no-name-in-module
"""A werewolf game implemented by agentscope."""
import asyncio
import os

from structured_output import (
    DiscussionModel,
    get_vote_model,
    get_poison_model,
    WitchResurrectModel,
    get_seer_model,
    get_hunter_model,
)
from prompt import Prompts
from utils import (
    check_winning,
    majority_vote,
    get_player_name,
    names_to_str,
    EchoAgent,
    MAX_GAME_ROUND,
    MAX_DISCUSSION_ROUND,
)
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import MsgHub, sequential_pipeline, fanout_pipeline

NAME_TO_ROLE = {}
moderator = EchoAgent()
healing, poison = True, True
villagers, werewolves, seer, witch, hunter = [], [], [], [], []
current_alive = []


async def hunter_stage(
    hunter_agent: ReActAgent,
) -> str | None:
    """Because the hunter's stage may happen in two places: killed at night
    or voted during the day, we define a function here to avoid duplication."""
    global current_alive, moderator
    msg_hunter = await hunter_agent(
        await moderator(Prompts.to_hunter.format(name=hunter_agent.name)),
        structured_model=get_hunter_model(current_alive),
    )
    if msg_hunter.metadata.get("shoot"):
        return msg_hunter.metadata.get("name", None)
    return None


def update_players(dead_players: list[str]) -> None:
    """Update the global alive players list by removing the dead players."""
    global werewolves, villagers, seer, hunter, witch, current_alive
    werewolves = [_ for _ in werewolves if _.name not in dead_players]
    villagers = [_ for _ in villagers if _.name not in dead_players]
    seer = [_ for _ in seer if _.name not in dead_players]
    hunter = [_ for _ in hunter if _.name not in dead_players]
    witch = [_ for _ in witch if _.name not in dead_players]
    current_alive = [_ for _ in current_alive if _.name not in dead_players]


async def create_player(role: str) -> ReActAgent:
    """Create a player with the given name and role."""
    name = get_player_name()
    global NAME_TO_ROLE
    NAME_TO_ROLE[name] = role
    agent = ReActAgent(
        name=name,
        sys_prompt=Prompts.system_prompt.format(
            player_name=name,
            guidance=getattr(Prompts, f"notes_{role}"),
        ),
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            enable_thinking=True,
        ),
        formatter=DashScopeMultiAgentFormatter(),
    )
    await agent.observe(
        await moderator(
            f"[{name} ONLY] {name}, your role is {role}.",
        ),
    )
    return agent


async def main() -> None:
    """The main entry of the werewolf game"""
    # Enable studio if you want
    # import agentscope
    # agentscope.init(
    #     studio_url="http://localhost:3000",
    #     project="Werewolf Game",
    # )
    global healing, poison, villagers, werewolves, seer, witch, hunter
    global current_alive
    # Create players
    villagers = [await create_player("villager") for _ in range(3)]
    werewolves = [await create_player("werewolf") for _ in range(3)]
    seer = [await create_player("seer")]
    witch = [await create_player("witch")]
    hunter = [await create_player("hunter")]
    # Speak in order of names
    current_alive = sorted(
        werewolves + villagers + seer + witch + hunter,
        key=lambda _: _.name,
    )

    # Game begin!
    for _ in range(MAX_GAME_ROUND):
        # Create a MsgHub for all players to broadcast messages
        async with MsgHub(
            participants=current_alive,
            enable_auto_broadcast=False,  # manual broadcast only
            name="all_players",
        ) as all_players_hub:
            # Night phase
            await all_players_hub.broadcast(
                await moderator(Prompts.to_all_night),
            )
            killed_player, poisoned_player, shot_player = None, None, None

            # Werewolves discuss
            async with MsgHub(
                werewolves,
                enable_auto_broadcast=True,
                announcement=await moderator(
                    Prompts.to_wolves_discussion.format(
                        names_to_str(werewolves),
                        names_to_str(current_alive),
                    ),
                ),
            ) as werewolves_hub:
                # Discussion
                res = None
                for _ in range(1, MAX_DISCUSSION_ROUND * len(werewolves) + 1):
                    res = await werewolves[_ % len(werewolves)](
                        structured_model=DiscussionModel,
                    )
                    if _ % len(werewolves) == 0 and res.metadata.get(
                        "reach_agreement",
                    ):
                        break

                # Werewolves vote
                # Disable auto broadcast to avoid following other's votes
                werewolves_hub.set_auto_broadcast(False)
                msgs_vote = await fanout_pipeline(
                    werewolves,
                    msg=await moderator(content=Prompts.to_wolves_vote),
                    structured_model=get_vote_model(current_alive),
                    enable_gather=False,
                )
                killed_player, votes = majority_vote(
                    [_.metadata.get("vote") for _ in msgs_vote],
                )
                # Postpone the broadcast of voting
                await werewolves_hub.broadcast(
                    [
                        *msgs_vote,
                        await moderator(
                            Prompts.to_wolves_res.format(votes, killed_player),
                        ),
                    ],
                )

            # Witch's turn
            await all_players_hub.broadcast(
                await moderator(Prompts.to_all_witch_turn),
            )
            msg_witch_poison = None
            for agent in witch:
                # Cannot heal witch herself
                msg_witch_resurrect = None
                if healing and killed_player != agent.name:
                    msg_witch_resurrect = await agent(
                        await moderator(
                            Prompts.to_witch_resurrect.format(
                                witch_name=agent.name,
                                dead_name=killed_player,
                            ),
                        ),
                        structured_model=WitchResurrectModel,
                    )
                    if msg_witch_resurrect.metadata.get("resurrect"):
                        killed_player = None
                        healing = False

                if poison and not (
                    msg_witch_resurrect
                    and msg_witch_resurrect.metadata["resurrect"]
                ):
                    msg_witch_poison = await agent(
                        await moderator(
                            Prompts.to_witch_poison.format(
                                witch_name=agent.name,
                            ),
                        ),
                        structured_model=get_poison_model(current_alive),
                    )
                    if msg_witch_poison.metadata.get("poison"):
                        poisoned_player = msg_witch_poison.metadata.get("name")
                        poison = False

            # Seer's turn
            await all_players_hub.broadcast(
                await moderator(Prompts.to_all_seer_turn),
            )
            for agent in seer:
                msg_seer = await agent(
                    await moderator(
                        Prompts.to_seer.format(
                            agent.name,
                            names_to_str(current_alive),
                        ),
                    ),
                    structured_model=get_seer_model(current_alive),
                )
                if msg_seer.metadata.get("name"):
                    player = msg_seer.metadata["name"]
                    await agent.observe(
                        await moderator(
                            Prompts.to_seer_result.format(
                                agent_name=player,
                                role=NAME_TO_ROLE[player],
                            ),
                        ),
                    )

            # Hunter's turn
            for agent in hunter:
                # If killed and not by witch's poison
                if (
                    killed_player == agent.name
                    and poisoned_player != agent.name
                ):
                    shot_player = await hunter_stage(agent)

            # Update alive players
            dead_tonight = [killed_player, poisoned_player, shot_player]
            update_players(dead_tonight)

            # Day phase
            if len([_ for _ in dead_tonight if _]) > 0:
                await all_players_hub.broadcast(
                    await moderator(
                        Prompts.to_all_day.format(
                            names_to_str([_ for _ in dead_tonight if _]),
                        ),
                    ),
                )
            else:
                await all_players_hub.broadcast(
                    await moderator(Prompts.to_all_peace),
                )

            # Check winning
            res = check_winning(current_alive, werewolves)
            if res:
                await moderator(res)
                return

            # Discussion
            await all_players_hub.broadcast(
                await moderator(
                    Prompts.to_all_discuss.format(
                        names=names_to_str(current_alive),
                    ),
                ),
            )
            # Open the auto broadcast to enable discussion
            all_players_hub.set_auto_broadcast(True)
            await sequential_pipeline(current_alive)
            # Disable auto broadcast to avoid leaking info
            all_players_hub.set_auto_broadcast(False)

            # Voting
            msgs_vote = await fanout_pipeline(
                current_alive,
                await moderator(
                    Prompts.to_all_vote.format(names_to_str(current_alive)),
                ),
                structured_model=get_vote_model(current_alive),
                enable_gather=False,
            )
            voted_player, votes = majority_vote(
                [_.metadata.get("vote") for _ in msgs_vote],
            )
            await all_players_hub.broadcast(
                [
                    *msgs_vote,
                    await moderator(
                        Prompts.to_all_res.format(votes, voted_player),
                    ),
                ],
            )

            shot_player = None
            for agent in hunter:
                if voted_player == agent.name:
                    shot_player = await hunter_stage(agent)
                    if shot_player:
                        await all_players_hub.broadcast(
                            await moderator(
                                Prompts.to_all_hunter_shoot.format(
                                    shot_player,
                                ),
                            ),
                        )

            # Update alive players
            dead_today = [voted_player, shot_player]
            update_players(dead_today)

            # Check winning
            res = check_winning(current_alive, werewolves)
            if res:
                await moderator(res)
                return


if __name__ == "__main__":
    asyncio.run(main())
