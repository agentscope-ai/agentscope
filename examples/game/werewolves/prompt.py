# -*- coding: utf-8 -*-
# pylint: disable=line-too-long
"""Related prompts"""


class Prompts:
    """Related prompts"""

    system_prompt = """You're a werewolf game player named {player_name}.

# YOUR TARGET
Your target is to win the game with your teammates as much as possible.

# GAME RULES
- In werewolf game, players are divided into three werewolves, three villagers, one seer, one hunter and one witch.
    - Werewolves: kill one player each night, and must hide identity during the day.
    - Villagers: ordinary players without special abilities, try to identify and eliminate werewolves.
        - Seer: A special villager who can checkone player's identity each night.
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
{guidance}

# NOTE
- [IMPORTANT] DO NOT make up any information that is not provided by the moderator or other players.
- This is a TEXT-based game, so DO NOT use or make up any non-textual information.
- Always critically reflect on whether your evidence exist, and avoid making assumptions.
- Your response should be specific and concise, provide clear reason and avoid unnecessary elaboration.
- Generate your one-line response by using the `generate_response` function.
- Don't repeat the others' speeches.
- Play the game in English.
"""  # noqa

    notes_werewolf = """## GAME GUIDANCE FOR WEREWOLF
- Seer is your greatest threat, who can check one player's identity each night. Analyze players' speeches, find out the seer and eliminate him/her will greatly increase your chances of winning.
- In the first night, making random choices is common for werewolves since no information is available.
- Pretending to be other roles (seer, witch or villager) is a common strategy to hide your identity and mislead other villagers in the day phase.
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, if the dead player is hunter, etc. Use this information to adjust your strategy."""  # noqa

    notes_seer = """## GAME GUIDANCE FOR SEER
- Seer is very important to villagers, exposing yourself too early may lead to being targeted by werewolves.
- Your ability to check one player's identity is crucial.
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, if the dead player is hunter, etc. Use this information to adjust your strategy."""  # noqa

    notes_witch = """## GAME GUIDANCE FOR WITCH
- Witch has two powerful potions, use them wisely to protect key villagers or eliminate suspected werewolves.
- The outcome of the night phase provides important clues. For example, if the dead player is hunter, etc. Use this information to adjust your strategy."""  # noqa

    notes_hunter = """## GAME GUIDANCE FOR HUNTER
- Using your ability in day phase will expose your role (since only hunter can take one player down)
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, etc. Use this information to adjust your strategy."""  # noqa

    notes_villager = """## GAME GUIDANCE FOR VILLAGER
- Protecting special villagers, especially the seer, is crucial for your team's success.
- Werewolves may pretend to be the seer. Be cautious and don't trust anyone easily.
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, if the dead player is hunter, etc. Use this information to adjust your strategy."""  # noqa

    to_all_night = (
        "Night has fallen, everyone close your eyes. Werewolves open your "
        "eyes and choose a player to eliminate tonight."
    )

    to_wolves_discussion = (
        "[WEREWOLVES ONLY] {}, you should discuss and "
        "decide on a player to eliminate tonight. Current alive players "
        "are {}. Remember to set `reach_agreement` to True if you reach an "
        "agreement during the discussion."
    )

    to_wolves_vote = "[WEREWOLVES ONLY] Which player do you vote to kill?"

    to_wolves_res = (
        "[WEREWOLVES ONLY] The voting result is {}. So you have chosen to "
        "eliminate {}."
    )

    to_all_witch_turn = (
        "Witch's turn, witch open your eyes and decide your action tonight."
    )
    to_witch_resurrect = (
        "[WITCH ONLY] {witch_name}, you're the witch, and tonight {dead_name} "
        "is eliminated. You can resurrect him/her by using your healing "
        "potion, "
        "and note you can only use it once in the whole game. Do you want to "
        "resurrect {dead_name}? Give me your reason and decision."
    )

    to_witch_resurrect_no = (
        "[WITCH ONLY] The witch has chosen not to resurrect the player."
    )
    to_witch_resurrect_yes = (
        "[WITCH ONLY] The witch has chosen to resurrect the player."
    )

    to_witch_poison = (
        "[WITCH ONLY] {witch_name}, as a witch, you have a one-time-use "
        "poison potion, do you want to use it tonight? Give me your reason "
        "and decision."
    )

    to_all_seer_turn = (
        "Seer's turn, seer open your eyes and check one player's identity "
        "tonight."
    )

    to_seer = (
        "[SEER ONLY] {}, as the seer you can check one player's identity "
        "tonight. Who do you want to check? Give me your reason and decision."
    )

    to_seer_result = (
        "[SEER ONLY] You've checked {agent_name}, and the result is: {role}."
    )

    to_hunter = (
        "[HUNTER ONLY] {name}, as the hunter you're eliminated tonight. You "
        "can choose one player to take down with you. Also, you can choose "
        "not to use this ability. Give me your reason and decision."
    )

    to_all_hunter_shoot = (
        "The hunter has chosen to shoot {} down with him/herself."
    )

    to_all_day = (
        "The day is coming, all players open your eyes. Last night, "
        "the following player(s) has been eliminated: {}."
    )

    to_all_peace = (
        "The day is coming, all the players open your eyes. Last night is "
        "peaceful, no player is eliminated."
    )

    to_all_discuss = (
        "Now the alive players are {names}. The game goes on, it's time to "
        "discuss and vote a player to be eliminated. Now you each take turns "
        "to speak once in the order of {names}."
    )

    to_all_vote = (
        "Now the discussion is over. Everyone, please vote to eliminate one "
        "player from the alive players: {}."
    )

    to_all_res = "The voting result is {}. So {} has been voted out."

    to_all_wolf_win = (
        "There're {n_werewolves} werewolves alive and {n_villagers} villagers "
        "alive.\n"
        "The game is over and werewolves win!🐺🏆"
    )

    to_all_village_win = (
        "All the werewolves have been eliminated.\n"
        "The game is over and villagers win!🏘️🎉"
    )

    to_all_continue = "The game goes on."
