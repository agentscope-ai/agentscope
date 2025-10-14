# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches, too-many-statements, no-name-in-module
"""A werewolf game implemented by agentscope."""
import numpy as np

from utils import (
    majority_vote,
    names_to_str,
    EchoAgent,
    MAX_GAME_ROUND,
    MAX_DISCUSSION_ROUND,
    Players,
)
from structured_model import (
    DiscussionModel,
    get_vote_model,
    get_poison_model,
    WitchResurrectModel,
    get_seer_model,
    get_hunter_model,
)
from prompt import EnglishPrompts as Prompts

# Uncomment the following line to use Chinese prompts
# from prompt import ChinesePrompts as Prompts


from agentscope.agent import ReActAgent
from agentscope.pipeline import (
    MsgHub,
    sequential_pipeline,
    fanout_pipeline,
)


moderator = EchoAgent()


async def hunter_stage(
    hunter_agent: ReActAgent,
    players: Players,
) -> str | None:
    """Because the hunter's stage may happen in two places: killed at night
    or voted during the day, we define a function here to avoid duplication."""
    global moderator
    msg_hunter = await hunter_agent(
        await moderator(Prompts.to_hunter.format(name=hunter_agent.name)),
        structured_model=get_hunter_model(players.current_alive),
    )
    if msg_hunter.metadata.get("shoot"):
        return msg_hunter.metadata.get("name", None)
    return None


async def werewolves_game(agents: list[ReActAgent]) -> None:
    """The main entry of the werewolf game

    Args:
        agents (`list[ReActAgent]`):
            A list of 9 agents.
    """
    assert len(agents) == 9, "The werewolf game needs exactly 9 players."

    # Init the players' status
    players = Players()

    # If the witch has healing and poison potion
    healing, poison = True, True

    # If it's the first day, the dead can leave a message
    first_day = True

    # Broadcast the game begin message
    async with MsgHub(participants=agents) as greeting_hub:
        await greeting_hub.broadcast(
            await moderator(
                Prompts.to_all_new_game.format(names_to_str(agents)),
            ),
        )

    # Assign roles to the agents
    roles = ["werewolf"] * 3 + ["villager"] * 3 + ["seer", "witch", "hunter"]
    np.random.shuffle(agents)
    np.random.shuffle(roles)

    for agent, role in zip(agents, roles):
        # Tell the agent its role
        await agent.observe(
            await moderator(
                f"[{agent.name} ONLY] {agent.name}, your role is {role}.",
            ),
        )
        players.add_player(agent, role)

    # Printing the roles
    players.print_roles()

    # GAME BEGIN!
    for _ in range(MAX_GAME_ROUND):
        # Create a MsgHub for all players to broadcast messages
        async with MsgHub(
            participants=players.current_alive,
            enable_auto_broadcast=False,  # manual broadcast only
            name="alive_players",
        ) as alive_players_hub:
            # Night phase
            await alive_players_hub.broadcast(
                await moderator(Prompts.to_all_night),
            )
            killed_player, poisoned_player, shot_player = None, None, None

            # Werewolves discuss
            async with MsgHub(
                players.werewolves,
                enable_auto_broadcast=True,
                announcement=await moderator(
                    Prompts.to_wolves_discussion.format(
                        names_to_str(players.werewolves),
                        names_to_str(players.current_alive),
                    ),
                ),
                name="werewolves",
            ) as werewolves_hub:
                # Discussion
                n_werewolves = len(players.werewolves)
                for _ in range(1, MAX_DISCUSSION_ROUND * n_werewolves + 1):
                    res = await players.werewolves[_ % n_werewolves](
                        structured_model=DiscussionModel,
                    )
                    if _ % n_werewolves == 0 and res.metadata.get(
                        "reach_agreement",
                    ):
                        break

                # Werewolves vote
                # Disable auto broadcast to avoid following other's votes
                werewolves_hub.set_auto_broadcast(False)
                msgs_vote = await fanout_pipeline(
                    players.werewolves,
                    msg=await moderator(content=Prompts.to_wolves_vote),
                    structured_model=get_vote_model(players.current_alive),
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
            await alive_players_hub.broadcast(
                await moderator(Prompts.to_all_witch_turn),
            )
            msg_witch_poison = None
            for agent in players.witch:
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

                # Has poison potion and hasn't used the healing potion
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
                        structured_model=get_poison_model(
                            players.current_alive,
                        ),
                    )
                    if msg_witch_poison.metadata.get("poison"):
                        poisoned_player = msg_witch_poison.metadata.get("name")
                        poison = False

            # Seer's turn
            await alive_players_hub.broadcast(
                await moderator(Prompts.to_all_seer_turn),
            )
            for agent in players.seer:
                msg_seer = await agent(
                    await moderator(
                        Prompts.to_seer.format(
                            agent.name,
                            names_to_str(players.current_alive),
                        ),
                    ),
                    structured_model=get_seer_model(players.current_alive),
                )
                if msg_seer.metadata.get("name"):
                    player = msg_seer.metadata["name"]
                    await agent.observe(
                        await moderator(
                            Prompts.to_seer_result.format(
                                agent_name=player,
                                role=players.name_to_role[player],
                            ),
                        ),
                    )

            # Hunter's turn
            for agent in players.hunter:
                # If killed and not by witch's poison
                if (
                    killed_player == agent.name
                    and poisoned_player != agent.name
                ):
                    shot_player = await hunter_stage(agent, players)

            # Update alive players
            dead_tonight = [killed_player, poisoned_player, shot_player]
            players.update_players(dead_tonight)

            # Day phase
            if len([_ for _ in dead_tonight if _]) > 0:
                await alive_players_hub.broadcast(
                    await moderator(
                        Prompts.to_all_day.format(
                            names_to_str([_ for _ in dead_tonight if _]),
                        ),
                    ),
                )

                # The killed player leave a last message in first night
                if killed_player and first_day:
                    msg_moderator = await moderator(
                        Prompts.to_dead_player.format(killed_player),
                    )
                    await alive_players_hub.broadcast(msg_moderator)
                    # Leave a message
                    last_msg = await players.name_to_agent[killed_player]()
                    await alive_players_hub.broadcast(last_msg)

            else:
                await alive_players_hub.broadcast(
                    await moderator(Prompts.to_all_peace),
                )

            # Check winning
            res = players.check_winning()
            if res:
                await moderator(res)
                break

            # Discussion
            await alive_players_hub.broadcast(
                await moderator(
                    Prompts.to_all_discuss.format(
                        names=names_to_str(players.current_alive),
                    ),
                ),
            )
            # Open the auto broadcast to enable discussion
            alive_players_hub.set_auto_broadcast(True)
            await sequential_pipeline(players.current_alive)
            # Disable auto broadcast to avoid leaking info
            alive_players_hub.set_auto_broadcast(False)

            # Voting
            msgs_vote = await fanout_pipeline(
                players.current_alive,
                await moderator(
                    Prompts.to_all_vote.format(
                        names_to_str(players.current_alive),
                    ),
                ),
                structured_model=get_vote_model(players.current_alive),
                enable_gather=False,
            )
            voted_player, votes = majority_vote(
                [_.metadata.get("vote") for _ in msgs_vote],
            )
            # Broadcast the voting messages together to avoid influencing
            # each other
            voting_msgs = [
                *msgs_vote,
                await moderator(
                    Prompts.to_all_res.format(votes, voted_player),
                ),
            ]

            # Leave a message if voted
            if voted_player:
                prompt_msg = await moderator(
                    Prompts.to_dead_player.format(voted_player),
                )
                last_msg = await players.name_to_agent[voted_player](
                    prompt_msg,
                )
                voting_msgs.extend([prompt_msg, last_msg])

            await alive_players_hub.broadcast(voting_msgs)

            # If the voted player is the hunter, he can shoot someone
            shot_player = None
            for agent in players.hunter:
                if voted_player == agent.name:
                    shot_player = await hunter_stage(agent, players)
                    if shot_player:
                        await alive_players_hub.broadcast(
                            await moderator(
                                Prompts.to_all_hunter_shoot.format(
                                    shot_player,
                                ),
                            ),
                        )

            # Update alive players
            dead_today = [voted_player, shot_player]
            players.update_players(dead_today)

            # Check winning
            res = players.check_winning()
            if res:
                async with MsgHub(players.all_players) as all_players_hub:
                    res_msg = await moderator(res)
                    await all_players_hub.broadcast(res_msg)
                break

        # The day ends
        first_day = False

    # Game over, each player reflects
    await fanout_pipeline(
        agents=agents,
        msg=await moderator(Prompts.to_all_reflect),
    )
