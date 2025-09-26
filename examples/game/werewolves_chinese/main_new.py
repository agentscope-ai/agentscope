# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches, too-many-statements, no-name-in-module
"""A werewolf game implemented by agentscope."""
import asyncio
import os
import random
from typing import Any, Literal, Optional
import time
from structured_output import (
    DiscussionModel,
    get_vote_model,
    get_werewolf_vote_model,
    get_poison_model,
    WitchResurrectModel,
    get_seer_model,
    get_hunter_model,
    get_guard_model,
    get_sheriff_election_model,
    get_sheriff_badge_transfer_model,
    get_self_explode_model,
    get_withdraw_model
)
from prompt import Prompts
from utils import (
    # check_winning,
    majority_vote,
    majority_vote_with_sheriff,
    # get_player_name,
    names_to_str,
    reorder_speaking_order,
    handle_sheriff_election_tie,
    get_time_based_speaking_order,
    get_sheriff_based_speaking_order,
    get_death_based_speaking_order,
    EchoAgent,
    MAX_GAME_ROUND,
    MAX_DISCUSSION_ROUND,
)
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeMultiAgentFormatter, OpenAIMultiAgentFormatter
from agentscope.model import DashScopeChatModel, OpenAIChatModel
from agentscope.pipeline import MsgHub, sequential_pipeline, fanout_pipeline
import numpy as np

def get_player_name(agent_names) -> str:
    """Generate player name."""
    return agent_names.pop(np.random.randint(len(agent_names)))


def check_winning(
        alive_agents: list,
        wolf_agents: list,
        name_to_role: dict,
) -> tuple[str | None, bool | None]:
    """Check if the game is over and return the winning message (屠边规则)."""
    # 屠边规则：狼人阵营胜利条件
    # 1. 所有神职死亡 或 2. 所有村民死亡

    # 统计存活的神职和村民
    alive_gods = []  # 神职：预言家、女巫、猎人、守卫
    alive_villagers = []  # 村民

    for agent in alive_agents:
        role = name_to_role.get(agent.name, "")
        if role in ["seer", "witch", "hunter", "guard"]:
            alive_gods.append(agent)
        elif role == "villager":
            alive_villagers.append(agent)

    # 狼人阵营胜利条件
    if not alive_gods or not alive_villagers:
        return Prompts.to_all_wolf_win.format(
            n_werewolves=(
                    f"{len(wolf_agents)}"
                    + f"({names_to_str([_.name for _ in wolf_agents])})"
            ),
            n_villagers=len(alive_villagers),
            n_gods=len(alive_gods),
        ), True

    # 好人阵营胜利条件：所有狼人（包括狼王）死亡
    if not wolf_agents:
        return Prompts.to_all_village_win, False

    return None, None

async def notify_hunter_poison_status(hunter_agent: ReActAgent, is_poisoned: bool, moderator: EchoAgent) -> None:
    """夜晚告知猎人是否中毒"""
    if is_poisoned:
        await hunter_agent.observe(
            await moderator(f"[仅猎人可见] {hunter_agent.name}，你被女巫毒死了，无法开枪。"),
        )
    else:
        await hunter_agent.observe(
            await moderator(f"[仅猎人可见] {hunter_agent.name}，你没有被毒死，可以开枪。"),
        )

async def hunter_stage(
        hunter_agent: ReActAgent,
        moderator: EchoAgent,
        current_alive: list[ReActAgent],
) -> str | None:
    """Because the hunter's stage may happen in two places: killed at night
    or voted during the day, we define a function here to avoid duplication."""
    msg_hunter = await hunter_agent(
        await moderator(Prompts.to_hunter.format(name=hunter_agent.name)),
        structured_model=get_hunter_model(current_alive),
    )
    if msg_hunter.metadata.get("shoot"):
        return msg_hunter.metadata.get("name", None)
    return None


async def notify_wolf_king_poison_status(wolf_king_agent: ReActAgent, is_poisoned: bool, moderator: EchoAgent) -> None:
    """夜晚告知狼王是否中毒"""
    if is_poisoned:
        await wolf_king_agent.observe(
            await moderator(f"[仅狼王可见] {wolf_king_agent.name}，你被女巫毒死了，无法开枪。"),
        )
    else:
        await wolf_king_agent.observe(
            await moderator(f"[仅狼王可见] {wolf_king_agent.name}，你没有被毒死，可以开枪。"),
        )


async def wolf_king_stage(
        wolf_king_agent: ReActAgent,
        moderator: EchoAgent,
        current_alive: list[ReActAgent],
) -> str | None:
    """Because the hunter's stage may happen in two places: killed at night
    or voted during the day, we define a function here to avoid duplication."""
    msg_wolf_king = await wolf_king_agent(
        await moderator(Prompts.to_wolf_king.format(name=wolf_king_agent.name)),
        structured_model=get_hunter_model(current_alive),
    )
    if msg_wolf_king.metadata.get("shoot"):
        return msg_wolf_king.metadata.get("name", None)
    return None


async def handle_self_explode(agent: ReActAgent, all_players_hub: MsgHub, moderator: EchoAgent,
                              NAME_TO_ROLE: dict) -> bool:
    """处理狼人自爆逻辑，返回是否自爆"""

    # 只有普通狼人可以自爆，狼王不能自爆
    if NAME_TO_ROLE[agent.name] != "werewolf":
        return False

    # 询问是否自爆（使用observe确保只有该狼人可见）
    await agent.observe(
        await moderator(f"【仅{agent.name}可见】，你是否要自爆？自爆将直接结束白天进入黑夜。"),
    )

    # 获取自爆回复（直接调用reply方法，避免广播）
    msg = await agent.reply(
        await moderator("请回复你的自爆决定。"),
        structured_model=get_self_explode_model(),
    )

    if msg.metadata.get("self_explode", False):
        # 自爆
        await all_players_hub.broadcast(
            await moderator(Prompts.to_self_explode_announcement.format(agent.name)),
        )
        return True

    return False


async def handle_last_words(all_players_hub: MsgHub, dead_players: list[str], moderator: EchoAgent,
                            all_players_original_order: list[ReActAgent], is_voted: bool = False,
                            is_gunned: bool = False) -> None:
    """处理遗言逻辑"""

    for dead_player in dead_players:
        if not dead_player:
            continue

        # 找到死亡的玩家
        dead_agent = None
        for agent in all_players_original_order:
            if agent.name == dead_player:
                dead_agent = agent
                break

        if dead_agent:
            # 判断是否有遗言
            if is_voted:
                # 每天被投票出局的玩家都有遗言
                await all_players_hub.broadcast(
                    await moderator(Prompts.to_last_words_vote.format(dead_player)),
                )
            elif is_gunned:
                # 每天被开枪出局的玩家都有遗言
                await all_players_hub.broadcast(
                    await moderator(Prompts.to_last_words_shoot.format(dead_player)),
                )
            else:
                # 首夜死亡的玩家有遗言（其他情况没有遗言）
                await all_players_hub.broadcast(
                    await moderator(Prompts.to_last_words.format(dead_player)),
                )

            # 让死亡的玩家发表遗言（遗言应该所有人可见）
            last_words_msg = await dead_agent(
                await moderator(f"{dead_player}，请发表你的遗言。"),
            )
            # 将遗言广播给所有人
            await all_players_hub.broadcast(last_words_msg)


async def choose_sheriff_speaking_order_for_discussion(all_players_hub: MsgHub, sheriff: str, has_death: bool,
                                                       moderator: EchoAgent, sheriff_speaking_direction: bool,
                                                       current_alive: list[ReActAgent]) -> bool:
    """警长选择讨论发言顺序（有死亡或平安夜时）"""

    # 找到警长玩家
    sheriff_agent = None
    for agent in current_alive:
        if agent.name == sheriff:
            sheriff_agent = agent
            break

    if sheriff_agent:
        if has_death:
            # 有死亡时，选择从死左还是死右开始
            msg = await sheriff_agent(
                await moderator(f"{sheriff}，请选择发言顺序：'死右'或'死左'。"),
            )
        else:
            # 平安夜时，选择从警左还是警右开始
            msg = await sheriff_agent(
                await moderator(f"{sheriff}，请选择发言顺序：'警右'或'警左'。"),
            )

        sheriff_speaking_direction = True  # 默认顺时针
        if msg.content and ("死左" in msg.content or "警左" in msg.content):
            sheriff_speaking_direction = False

        direction = "死右" if (has_death and sheriff_speaking_direction) else "死左" if (
                has_death and not sheriff_speaking_direction) else "警右" if (
                not has_death and sheriff_speaking_direction) else "警左"
        await all_players_hub.broadcast(
            await moderator(f"警长{sheriff}选择{direction}发言顺序。"),
        )
        return sheriff_speaking_direction
    return sheriff_speaking_direction


async def choose_sheriff_speaking_order(all_players_hub: MsgHub, sheriff: str, moderator: EchoAgent,
                                        sheriff_speaking_direction: bool, current_alive: list[ReActAgent]) -> bool:
    """警长选择发言顺序"""

    # 找到警长玩家
    sheriff_agent = None
    for agent in current_alive:
        if agent.name == sheriff:
            sheriff_agent = agent
            break

    if sheriff_agent:
        # 警长选择发言顺序
        msg = await sheriff_agent(
            await moderator(f"{sheriff}，请选择发言顺序：'顺时针'或'逆时针'。"),
        )

        sheriff_speaking_direction = True  # 默认顺时针
        if msg.content and "逆时针" in msg.content:
            sheriff_speaking_direction = False

        direction = "顺时针" if sheriff_speaking_direction else "逆时针"
        await all_players_hub.broadcast(
            await moderator(f"警长{sheriff}选择{direction}发言顺序。"),
        )
        return sheriff_speaking_direction
    return sheriff_speaking_direction


async def handle_sheriff_death(dead_sheriff: str, all_players_hub: MsgHub, current_alive: list[ReActAgent],
                               sheriff: str, sheriff_has_badge: bool, moderator: EchoAgent,
                               all_players_original_order: list[ReActAgent]) -> tuple:
    """处理警长死亡时的警徽传递"""

    if dead_sheriff and sheriff_has_badge:
        # 找到警长玩家（在all_players_original_order中寻找，因为死亡的警长不在current_alive中）
        sheriff_agent = None
        for agent in all_players_original_order:
            if agent.name == dead_sheriff:
                sheriff_agent = agent
                break

        if sheriff_agent:
            # 警长选择传递警徽
            msg_badge = await sheriff_agent(
                await moderator(
                    Prompts.to_sheriff_death.format(
                        dead_sheriff, names_to_str(current_alive)
                    ),
                ),
                structured_model=get_sheriff_badge_transfer_model(current_alive),
            )

            badge_choice = msg_badge.metadata.get("name")
            if badge_choice and badge_choice != "撕警徽":
                # 传递警徽
                sheriff = badge_choice
                await all_players_hub.broadcast(
                    await moderator(Prompts.to_sheriff_badge_transfer.format(dead_sheriff, badge_choice)),
                )
                return sheriff, sheriff_has_badge
            else:
                # 撕警徽
                sheriff = None
                sheriff_has_badge = False
                await all_players_hub.broadcast(
                    await moderator(Prompts.to_sheriff_badge_destroy.format(dead_sheriff)),
                )
                return sheriff, sheriff_has_badge
        return sheriff, sheriff_has_badge


def update_players(dead_players: list[str], werewolves: list[ReActAgent], villagers: list[ReActAgent],
                   seer: list[ReActAgent], hunter: list[ReActAgent], witch: list[ReActAgent], guard: list[ReActAgent],
                   wolf_king: list[ReActAgent], current_alive: list[ReActAgent],
                   full_werewolves: list[ReActAgent]) -> tuple:
    """Update the global alive players list by removing the dead players."""
    werewolves = [_ for _ in werewolves if _.name not in dead_players]
    villagers = [_ for _ in villagers if _.name not in dead_players]
    seer = [_ for _ in seer if _.name not in dead_players]
    hunter = [_ for _ in hunter if _.name not in dead_players]
    witch = [_ for _ in witch if _.name not in dead_players]
    guard = [_ for _ in guard if _.name not in dead_players]
    wolf_king = [_ for _ in wolf_king if _.name not in dead_players]
    current_alive = [_ for _ in current_alive if _.name not in dead_players]
    full_werewolves = [_ for _ in full_werewolves if _.name not in dead_players]
    return werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves


async def create_player(role: str, NAME_TO_ROLE: dict, ch_names: dict, moderator: EchoAgent, agent_names: list[str]) -> tuple:
    """Create a player with the given name and role."""
    name = get_player_name(agent_names)
    NAME_TO_ROLE[name] = role
    # 添加外部对抗性
    import random
    # if random.random() < 0.8:
    #     agent = ReActAgent(
    #         name=name,
    #         sys_prompt=Prompts.system_prompt.format(
    #             player_name=name,
    #             guidance=getattr(Prompts, f"notes_{role}"),
    #         ),
    #         # model=DashScopeChatModel(
    #         #     model_name="qwen3-max-preview",
    #         #     api_key=os.environ["DASHSCOPE_API_KEY"],
    #         #     enable_thinking=True,
    #         # ),
    #         # model=OpenAIChatModel(
    #         #     model_name="/root/dataDisk/Qwen3-8B",
    #         #     client_args={"base_url": "http://127.0.0.1:8000/v1"},
    #         #     api_key="xxx",
    #         #     stream=False,
    #         # ),
    #         model=OpenAIChatModel(
    #             model_name=llm.model,
    #             client_args={"base_url": llm.endpoint},
    #             api_key="xxx",
    #             stream=False,
    #         ),
    #         # formatter=DashScopeMultiAgentFormatter(),
    #         formatter=OpenAIMultiAgentFormatter(),
    #     )
    # else:
    agent = ReActAgent(
        name=name,
        sys_prompt=Prompts.system_prompt.format(
            player_name=name,
            guidance=getattr(Prompts, f"notes_{role}"),
        ),
        model=DashScopeChatModel(
            model_name="qwen3-max-preview",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            enable_thinking=True,
        ),
        formatter=DashScopeMultiAgentFormatter(),
    )
    # 获取对应角色的游戏指南
    role_notes = getattr(Prompts, f"notes_{role}", "")

    await agent.observe(
        await moderator(
            f"[{name} ONLY] 你是{name}, 你的角色是 {ch_names[role]}.\n\n{role_notes}",
        ),
    )
    return agent, NAME_TO_ROLE, agent_names

async def main() -> None:
    """The main entry of the werewolf game"""
    # Enable studio if you want
    # import agentscope
    # agentscope.init(
    #     studio_url="http://localhost:3000",
    #     project="Werewolf Game",
    # )
    start_time = time.time()
    agent_names = ["1号", "2号", "3号", "4号", "5号", "6号", "7号", "8号", "9号", "10号", "11号", "12号"]
    NAME_TO_ROLE = {}
    moderator = EchoAgent()
    healing, poison = True, True
    villagers, werewolves, seer, witch, hunter, guard, wolf_king = [], [], [], [], [], [], []
    current_alive = []
    all_players_original_order = []  # 保存所有玩家的原始顺序
    gun_players = []  # 保存有开枪技能的玩家（猎人和狼王），不受update_players影响
    sheriff = None  # 当前警长
    sheriff_has_badge = False  # 是否有警徽
    sheriff_speaking_direction = True  # 警长选择的发言方向，True为顺时针，False为逆时针
    first_explode_interrupted_election = False  # 是否第一次自爆打断了警长竞选
    first_day_candidates = []  # 第一天的上警玩家
    ch_names = {"werewolf": "狼人", "villager": "村民", "seer": "预言家", "witch": "女巫", "hunter": "猎人", "guard": "守卫",
                "wolf_king": "狼王"}

    # 发言顺序配置
    # 可选值: "from_death" (从死者位置开始), "reverse" (逆序), "original" (原顺序)
    SPEAKING_ORDER_TYPE = "from_death"

    # Create players
    # villagers = [await create_player("villager") for _ in range(4)]
    for _ in range(4):
        a, NAME_TO_ROLE, agent_names = await create_player("villager", NAME_TO_ROLE, ch_names, moderator,
                                                               agent_names)
        villagers.append(a)
    # werewolves = [await create_player("werewolf") for _ in range(3)]
    for _ in range(3):
        a, NAME_TO_ROLE, agent_names = await create_player("werewolf", NAME_TO_ROLE, ch_names, moderator,
                                                               agent_names)
        werewolves.append(a)
    # seer = [await create_player("seer")]
    a, NAME_TO_ROLE, agent_names = await create_player("seer", NAME_TO_ROLE, ch_names, moderator,
                                                           agent_names)
    seer.append(a)
    # witch = [await create_player("witch")]
    a, NAME_TO_ROLE, agent_names = await create_player("witch", NAME_TO_ROLE, ch_names, moderator,
                                                           agent_names)
    witch.append(a)
    # hunter = [await create_player("hunter")]
    a, NAME_TO_ROLE, agent_names = await create_player("hunter", NAME_TO_ROLE, ch_names, moderator,
                                                           agent_names)
    hunter.append(a)
    # guard = [await create_player("guard")]
    a, NAME_TO_ROLE, agent_names = await create_player("guard", NAME_TO_ROLE, ch_names, moderator,
                                                           agent_names)
    guard.append(a)
    # wolf_king = [await create_player("wolf_king")]
    a, NAME_TO_ROLE, agent_names = await create_player("wolf_king", NAME_TO_ROLE, ch_names, moderator,
                                                           agent_names)
    wolf_king.append(a)
    # Speak in order of names
    current_alive = sorted(
        werewolves + villagers + seer + witch + hunter + guard + wolf_king,
        key=lambda _: _.name,
    )

    # 保存所有玩家的原始顺序（包括死亡的玩家，用于遗言等功能）
    all_players_original_order = current_alive.copy()

    # 初始化有开枪技能的玩家列表
    gun_players = (hunter + wolf_king).copy()

    # Randomize the order of players
    last_guarded_player = None

    # Game begin!
    for round_num in range(MAX_GAME_ROUND):
        exploded_agent = None
        dead_today = []
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
            killed_player, healed_player, poisoned_player, shot_player, shot_player2, guarded_player = None, None, None, None, None, None

            # Guard's turn
            await all_players_hub.broadcast(
                await moderator(Prompts.to_all_guard_turn),
            )
            for agent in guard:
                msg_guard = await agent(
                    await moderator(
                        Prompts.to_guard_action.format(
                            agent_name=agent.name,
                            last_guarded_player=last_guarded_player,
                            current_alive=names_to_str(current_alive),
                        ),
                    ),
                    structured_model=get_guard_model(current_alive),
                )
                guard_choice = msg_guard.metadata.get("name")
                if guard_choice and guard_choice != "空守" and guard_choice != last_guarded_player:
                    guarded_player = guard_choice
                    last_guarded_player = guarded_player
                    # 只给守卫自己发送结果
                    await agent.observe(
                        await moderator(
                            Prompts.to_guard_result.format(agent_name=guarded_player),
                        ),
                    )
                elif guard_choice == "空守":
                    # 只给守卫自己发送空守结果
                    guarded_player = None
                    last_guarded_player = None
                    await agent.observe(
                        await moderator(Prompts.to_guard_empty),
                    )
                elif guard_choice == last_guarded_player:
                    # 只给守卫自己发送结果
                    guarded_player = None
                    last_guarded_player = None
                    await agent.observe(
                        await moderator(Prompts.to_guard_failed.format(agent_name=last_guarded_player)),
                    )

            await all_players_hub.broadcast(
                await moderator(Prompts.to_all_werewolves_turn),
            )
            full_werewolves = werewolves + wolf_king
            # Werewolves discuss
            # 为每个狼人准备队友和狼王信息
            wolf_king_name = names_to_str(wolf_king) if wolf_king else "无"
            werewolf_names = names_to_str(werewolves) if werewolves else "无"

            async with MsgHub(
                    full_werewolves,
                    enable_auto_broadcast=True,
                    announcement=await moderator(
                        Prompts.to_wolves_discussion.format(
                            names_to_str(full_werewolves),
                            werewolf_names,
                            wolf_king_name,
                            names_to_str(current_alive),
                        ),
                    ),
            ) as werewolves_hub:
                # Discussion
                res = None
                for _ in range(1, MAX_DISCUSSION_ROUND * len(full_werewolves) + 1):
                    res = await full_werewolves[_ % len(full_werewolves)](
                        structured_model=DiscussionModel,
                    )
                    if _ % len(full_werewolves) == 0 and res.metadata.get(
                            "reach_agreement",
                    ):
                        break

                # Werewolves vote
                # Disable auto broadcast to avoid following other's votes
                werewolves_hub.set_auto_broadcast(False)
                msgs_vote = await fanout_pipeline(
                    full_werewolves,
                    msg=await moderator(content=Prompts.to_wolves_vote),
                    structured_model=get_werewolf_vote_model(current_alive),
                    enable_gather=False,
                )
                # 狼人投票使用简单模式
                killed_player, votes, is_tie = majority_vote(
                    [_.metadata.get("vote") for _ in msgs_vote],
                    detailed=False
                )

                # 处理狼人投票结果
                if killed_player is None:
                    # 所有狼人都选择空刀
                    result_msg = await moderator(
                        Prompts.to_wolves_empty.format(votes),
                    )
                elif is_tie:
                    # 如果狼人投票平票，随机选择一个
                    import random
                    killed_player = random.choice(killed_player)
                    result_msg = await moderator(
                        Prompts.to_wolves_res.format(votes, killed_player),
                    )
                else:
                    # 有明确结果
                    result_msg = await moderator(
                        Prompts.to_wolves_res.format(votes, killed_player),
                    )

                # Postpone the broadcast of voting
                await werewolves_hub.broadcast(
                    [
                        *msgs_vote,
                        result_msg,
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
                        healed_player = killed_player
                        healing = False
                        if guarded_player != killed_player:
                            killed_player = None

                if poison and not (
                        msg_witch_resurrect
                        and msg_witch_resurrect.metadata["resurrect"]
                ):
                    if killed_player == agent.name and healing:
                        Prompts.to_witch_poison = "你中刀了，不能使用解药，只能使用毒药。" + Prompts.to_witch_poison
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
                                role="好人" if NAME_TO_ROLE[player] not in ["werewolf", "wolf_king"] else "狼人",
                            ),
                        ),
                    )

            if killed_player:
                if killed_player == guarded_player and killed_player != poisoned_player and killed_player != healed_player:
                    killed_player = None

            # Hunter's turn - 夜晚只告知中毒状态，不能开枪
            for agent in hunter:
                # 告知猎人是否中毒（无论是否被击杀）
                is_poisoned = (poisoned_player == agent.name)
                await notify_hunter_poison_status(agent, is_poisoned, moderator)

            # Wolf King's turn - 夜晚只告知中毒状态，不能开枪
            for agent in wolf_king:
                # 告知狼王是否中毒（无论是否被击杀）
                is_poisoned = (poisoned_player == agent.name)
                await notify_wolf_king_poison_status(agent, is_poisoned, moderator)

            # Update alive players
            dead_tonight = [killed_player, poisoned_player]
            dead_tonight = [player for player in dead_tonight if player is not None]
            if dead_tonight:

                werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                    dead_tonight, werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive,
                    full_werewolves)

                # 检查胜利条件
                res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                if res:
                    await moderator(res)
                    return

                dead_sheriff = None
                if sheriff and sheriff in dead_tonight:
                    dead_sheriff = sheriff
                    sheriff, sheriff_has_badge = await handle_sheriff_death(dead_sheriff, all_players_hub,
                                                                            current_alive, sheriff,
                                                                            sheriff_has_badge, moderator,
                                                                            all_players_original_order)

            # 警长竞选（仅在首夜后的第一个白天，在死讯公布之前）
            if round_num == 0 and not sheriff:
                """警长竞选环节"""

                # 询问谁要上警
                await all_players_hub.broadcast(
                    await moderator("现在开始警长竞选，请想要上警的玩家举手。"),
                )

                # 让所有玩家决定是否上警
                candidates = []
                for agent in all_players_original_order:
                    msg = await agent(
                        await moderator(f"{agent.name}，你是否要上警竞选警长？请仅回答'是'或'否'。"),
                    )
                    if msg.content and ("是" in msg.content or "上警" in msg.content or "竞选" in msg.content):
                        candidates.append(agent)

                if not candidates:
                    await all_players_hub.broadcast(
                        await moderator(Prompts.to_all_election_failed),
                    )
                else:
                    # 保存第一天的上警玩家信息（用于双爆吞警徽）
                    first_day_candidates = [agent.name for agent in candidates]

                    # 宣布候选人
                    candidate_names = names_to_str(candidates)
                    await all_players_hub.broadcast(
                        await moderator(Prompts.to_all_election.format(candidate_names)),
                    )

                    # Open the auto broadcast to enable discussion
                    all_players_hub.set_auto_broadcast(True)

                    # 候选人依次发言（基于时间决定顺序）
                    speaking_order_candidates = get_time_based_speaking_order(candidates)
                    for agent in speaking_order_candidates:
                        # 检查是否自爆
                        if await handle_self_explode(agent, all_players_hub, moderator, NAME_TO_ROLE):
                            # 自爆了，处理自爆玩家的死亡逻辑
                            dead_today = [agent.name]
                            exploded_agent = agent
                            # 警长竞选阶段还没有警长，所以不需要检查警长死亡
                            werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                                dead_today, werewolves, villagers, seer, hunter, witch, guard, wolf_king,
                                current_alive, full_werewolves)

                            # 设置第一次自爆打断警长竞选的标志
                            first_explode_interrupted_election = True

                            # 检查胜利条件
                            res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                            if res:
                                await moderator(res)
                                return

                            break

                        await agent(
                            await moderator(Prompts.to_sheriff_candidate.format(agent.name)),
                        )

                    if exploded_agent:
                        # 检查胜利条件
                        res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                        if res:
                            await moderator(res)
                            return

                        # 不要直接跳过，还有遗言和开枪，下面处理

                    if not first_explode_interrupted_election:
                        # 发言完毕后，投票前统一询问退水
                        for agent in candidates:
                            # 检查是否退水
                            await agent.observe(
                                await moderator(f"【仅{agent.name}可见】，你是否要退水（退出警长竞选）？"),
                            )

                            msg = await agent.reply(
                                await moderator("请回复你的退水决定。"),
                                structured_model=get_withdraw_model(),
                            )

                            if msg.metadata.get("withdraw", False):
                                # 退水
                                # 从first_day_candidates中移除
                                if agent.name in first_day_candidates:
                                    first_day_candidates.remove(agent.name)
                                await all_players_hub.broadcast(
                                    await moderator(f"{agent.name}选择退水，退出警长竞选。"),
                                )

                        # 检查是否还有活跃的候选人
                        if not first_day_candidates:
                            await all_players_hub.broadcast(
                                await moderator("所有候选人都已退水，警徽流失。"),
                            )
                        else:

                            # 只有未上警的玩家可以投票
                            voting_agents = [agent for agent in all_players_original_order if
                                             agent.name not in first_day_candidates]

                            if not voting_agents:
                                # 全员上警，无人投票，警徽流失
                                await all_players_hub.broadcast(
                                    await moderator("全员上警，无人可以投票，警徽流失。"),
                                )
                            else:
                                # 投票前检查自爆
                                for agent in voting_agents:
                                    # 检查是否自爆
                                    if await handle_self_explode(agent, all_players_hub, moderator, NAME_TO_ROLE):
                                        exploded_agent = agent
                                        break

                                # 如果有人自爆，直接结束警长竞选，进入黑夜
                                if exploded_agent:
                                    # 自爆了，处理自爆玩家的死亡逻辑
                                    dead_today = [exploded_agent.name]
                                    # 警长竞选阶段还没有警长，所以不需要检查警长死亡
                                    werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                                        dead_today, werewolves, villagers, seer, hunter, witch, guard, wolf_king,
                                        current_alive, full_werewolves)

                                    # 设置第一次自爆打断警长竞选的标志
                                    first_explode_interrupted_election = True

                                    # 检查胜利条件
                                    res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                                    if res:
                                        await moderator(res)
                                        return

                                if not first_explode_interrupted_election:
                                    # 将first_day_candidates转换为agent对象列表
                                    active_candidates = []
                                    for name in first_day_candidates:
                                        for agent in all_players_original_order:
                                            if agent.name == name:
                                                active_candidates.append(agent)
                                                break

                                    # Disable auto broadcast to avoid leaking info
                                    all_players_hub.set_auto_broadcast(False)

                                    await all_players_hub.broadcast(
                                        await moderator(
                                            Prompts.to_all_election_vote.format(names_to_str(active_candidates))),
                                    )

                                    msgs_election_vote = await fanout_pipeline(
                                        voting_agents,
                                        await moderator(
                                            Prompts.to_all_election_vote.format(names_to_str(active_candidates))),
                                        structured_model=get_sheriff_election_model(active_candidates),
                                        enable_gather=False,
                                    )

                                    # 处理投票结果
                                    votes_list = [_.metadata.get("vote") for _ in msgs_election_vote]
                                    voters_list = [_.name for _ in msgs_election_vote]
                                    elected_sheriff, votes, is_tie = majority_vote(
                                        votes_list, detailed=True, voters=voters_list
                                    )

                                    # await all_players_hub.broadcast(msgs_election_vote)

                                    if is_tie:
                                        # 平票，进入PK环节
                                        # Open the auto broadcast to enable discussion
                                        all_players_hub.set_auto_broadcast(True)

                                        tied_candidates = [candidate.name for candidate in active_candidates if
                                                           candidate.name in elected_sheriff]
                                        await all_players_hub.broadcast(
                                            await moderator(
                                                Prompts.to_all_election_tie.format(votes,
                                                                                   names_to_str(tied_candidates))),
                                        )

                                        # PK发言
                                        await all_players_hub.broadcast(
                                            await moderator(
                                                Prompts.to_all_election_pk.format(names_to_str(tied_candidates))),
                                        )

                                        pk_candidates = [agent for agent in active_candidates if
                                                         agent.name in tied_candidates]

                                        # 在警上PK发言过程中检查自爆
                                        for agent in pk_candidates:
                                            # 检查是否自爆
                                            if await handle_self_explode(agent, all_players_hub, moderator,
                                                                         NAME_TO_ROLE):
                                                exploded_agent = agent
                                                break

                                            # 正常发言
                                            await agent(
                                                await moderator(f"{agent.name}，请发言。"),
                                            )

                                        # 如果有人自爆，直接结束警长竞选，进入黑夜
                                        if exploded_agent:
                                            # 自爆了，处理自爆玩家的死亡逻辑
                                            dead_today = [exploded_agent.name]
                                            # 警长竞选阶段还没有警长，所以不需要检查警长死亡
                                            werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                                                dead_today, werewolves, villagers, seer, hunter, witch, guard,
                                                wolf_king,
                                                current_alive, full_werewolves)

                                            # 设置第一次自爆打断警长竞选的标志
                                            first_explode_interrupted_election = True

                                            # 检查胜利条件
                                            res, wolf_win_flag = check_winning(current_alive, full_werewolves,
                                                                               NAME_TO_ROLE)
                                            if res:
                                                await moderator(res)
                                                return

                                        if not first_explode_interrupted_election:
                                            await sequential_pipeline(pk_candidates)
                                            # PK投票
                                            await all_players_hub.broadcast(
                                                await moderator(
                                                    Prompts.to_all_election_pk_vote.format(
                                                        names_to_str(tied_candidates))),
                                            )

                                            # PK环节：除了PK台上的玩家，其他所有玩家都可以投票（包括上警的玩家）
                                            voting_agents = [agent for agent in current_alive if
                                                             agent.name not in tied_candidates]

                                            # 投票前检查自爆
                                            for agent in voting_agents:
                                                # 检查是否自爆
                                                if await handle_self_explode(agent, all_players_hub, moderator,
                                                                             NAME_TO_ROLE):
                                                    exploded_agent = agent
                                                    break

                                            # 如果有人自爆，直接结束警长竞选，进入黑夜
                                            if exploded_agent:
                                                # 自爆了，处理自爆玩家的死亡逻辑
                                                dead_today = [exploded_agent.name]
                                                # 警长竞选阶段还没有警长，所以不需要检查警长死亡
                                                werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                                                    dead_today, werewolves, villagers, seer, hunter, witch, guard,
                                                    wolf_king,
                                                    current_alive, full_werewolves)

                                                # 设置第一次自爆打断警长竞选的标志
                                                first_explode_interrupted_election = True

                                                # 检查胜利条件
                                                res, wolf_win_flag = check_winning(current_alive, full_werewolves,
                                                                                   NAME_TO_ROLE)
                                                if res:
                                                    await moderator(res)
                                                    return

                                            if not first_explode_interrupted_election:
                                                # Disable auto broadcast to avoid leaking info
                                                all_players_hub.set_auto_broadcast(False)

                                                await all_players_hub.broadcast(
                                                    await moderator(
                                                        Prompts.to_all_election_pk_vote.format(
                                                            names_to_str(tied_candidates))),
                                                )

                                                msgs_pk_vote = await fanout_pipeline(
                                                    voting_agents,
                                                    await moderator(
                                                        Prompts.to_all_election_pk_vote.format(
                                                            names_to_str(tied_candidates))),
                                                    structured_model=get_sheriff_election_model(pk_candidates),
                                                    enable_gather=False,
                                                )

                                                # 处理PK投票结果
                                                pk_votes_list = [_.metadata.get("vote") for _ in msgs_pk_vote]
                                                pk_voters_list = [_.name for _ in msgs_pk_vote]
                                                pk_result, pk_votes, pk_is_tie = majority_vote(
                                                    pk_votes_list, detailed=True, voters=pk_voters_list
                                                )

                                                # await all_players_hub.broadcast(msgs_pk_vote)

                                                if pk_is_tie:
                                                    # PK也平票，警徽流失
                                                    await all_players_hub.broadcast(
                                                        await moderator(
                                                            Prompts.to_all_election_pk_tie.format(pk_votes,
                                                                                                  names_to_str(
                                                                                                      tied_candidates))),
                                                    )
                                                else:
                                                    # PK有结果
                                                    sheriff = pk_result
                                                    sheriff_has_badge = True
                                                    await all_players_hub.broadcast(
                                                        await moderator(
                                                            Prompts.to_all_election_pk_result.format(pk_votes,
                                                                                                     sheriff)),
                                                    )
                                                    # 警长选择发言顺序
                                                    sheriff_speaking_direction = await choose_sheriff_speaking_order(
                                                        all_players_hub, sheriff, moderator,
                                                        sheriff_speaking_direction, current_alive)
                                            else:
                                                # 有明确结果
                                                if elected_sheriff and elected_sheriff != "弃票":
                                                    sheriff = elected_sheriff
                                                    sheriff_has_badge = True
                                                    await all_players_hub.broadcast(
                                                        await moderator(
                                                            Prompts.to_all_election_result.format(sheriff, votes)),
                                                    )
                                                    # 警长选择发言顺序
                                                    sheriff_speaking_direction = await choose_sheriff_speaking_order(
                                                        all_players_hub, sheriff, moderator,
                                                        sheriff_speaking_direction, current_alive)
                                                else:
                                                    # 所有人弃票，警徽流失
                                                    await all_players_hub.broadcast(
                                                        await moderator("所有人弃票，警徽流失。"),
                                                    )

            if round_num == 1 and first_explode_interrupted_election:
                """第二天直接投票选举警长（双爆吞警徽逻辑）"""
                second_explode_interrupted_election = False
                # 获取第一天的上警玩家（还活着的）
                alive_candidates = []
                for name in first_day_candidates:
                    for agent in current_alive:
                        if agent.name == name:
                            alive_candidates.append(agent)
                            break

                if not alive_candidates:
                    await all_players_hub.broadcast(
                        await moderator("第一天的上警玩家都已死亡，警徽流失。"),
                    )
                else:
                    # 退水
                    for agent in alive_candidates:
                        await agent.observe(
                            await moderator(f"【仅{agent.name}可见】，你是否要退水（退出警长竞选）？"),
                        )
                        msg = await agent.reply(
                            await moderator("请回复你的退水决定。"),
                            structured_model=get_withdraw_model(),
                        )
                        if msg.metadata.get("withdraw", False):
                            alive_candidates.remove(agent)
                            # 从first_day_candidates中移除
                            if agent.name in first_day_candidates:
                                first_day_candidates.remove(agent.name)
                            await all_players_hub.broadcast(
                                await moderator(f"{agent.name}选择退水，退出警长竞选。"),
                            )
                    # 检查是否还有活跃的候选人
                    if not alive_candidates:
                        await all_players_hub.broadcast(
                            await moderator("所有候选人都已退水，警徽流失。"),
                        )
                    else:

                        # 宣布候选人
                        candidate_names = names_to_str(alive_candidates)
                        await all_players_hub.broadcast(
                            await moderator(f"由于昨天有狼人自爆打断了警长竞选，今天直接进行警长投票，不进行发言。候选人：{candidate_names}"),
                        )

                        # 检查是否有自爆打断投票
                        exploded_agent = None
                        for agent in current_alive:
                            if await handle_self_explode(agent, all_players_hub, moderator, NAME_TO_ROLE):
                                exploded_agent = agent
                                break

                        if exploded_agent:
                            # 第二次自爆，双爆吞警徽
                            dead_today = [exploded_agent.name]
                            werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                                dead_today, werewolves, villagers, seer, hunter, witch, guard, wolf_king,
                                current_alive, full_werewolves)
                            second_explode_interrupted_election = True
                            # 检查胜利条件
                            res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                            if res:
                                await moderator(res)
                                return

                            await all_players_hub.broadcast(
                                await moderator("双爆吞警徽！警徽流失，游戏继续。"),
                            )
                        if not second_explode_interrupted_election:
                            # 投票玩家排除候选
                            voting_agents = [agent for agent in current_alive if agent.name not in candidate_names]

                            # 让所有玩家直接投票选举警长
                            msgs_election_vote = await fanout_pipeline(
                                voting_agents,
                                await moderator("请所有玩家直接投票选举警长。"),
                                structured_model=get_sheriff_election_model(alive_candidates),
                                enable_gather=False,
                            )

                            # 处理投票结果
                            votes_list = [_.metadata.get("vote") for _ in msgs_election_vote]
                            # votes = {name: votes_list.count(name) for name in set(votes_list)}
                            voters_list = [_.name for _ in msgs_election_vote]

                            # 没有自爆，正常处理投票结果
                            elected_sheriff, votes, is_tie = majority_vote(
                                votes_list, detailed=True, voters=voters_list
                            )

                            if is_tie:
                                tied_candidates = [candidate.name for candidate in alive_candidates if
                                                   candidate.name in elected_sheriff]
                                await all_players_hub.broadcast(
                                    await moderator(
                                        Prompts.to_all_election_tie.format(votes, names_to_str(tied_candidates))),
                                )

                                await all_players_hub.broadcast(
                                    await moderator(f"第二天警长选举平票，不再有PK环节，警徽流失。"),
                                )

                            else:
                                # 有明确结果
                                if elected_sheriff and elected_sheriff != "弃票":
                                    sheriff = elected_sheriff
                                    sheriff_has_badge = True
                                    await all_players_hub.broadcast(
                                        await moderator(Prompts.to_all_election_result.format(sheriff, votes)),
                                    )
                                    # 警长选择发言顺序
                                    sheriff_speaking_direction = await choose_sheriff_speaking_order(
                                        all_players_hub,
                                        sheriff, moderator,
                                        sheriff_speaking_direction,
                                        current_alive)
                                else:
                                    # 所有人弃票，警徽流失
                                    await all_players_hub.broadcast(
                                        await moderator("所有人弃票，警徽流失。"),
                                    )
            # Day phase - 死讯公布（在警长竞选之后）
            if len([_ for _ in dead_tonight if _]) > 0:
                await all_players_hub.broadcast(
                    await moderator(
                        Prompts.to_all_day.format(
                            names_to_str([_ for _ in dead_tonight if _]),
                        ),
                    ),
                )

                # 处理夜晚死亡的遗言（首夜有遗言）
                if round_num == 0:
                    await handle_last_words(all_players_hub, dead_tonight, moderator, all_players_original_order)
            else:
                await all_players_hub.broadcast(
                    await moderator(Prompts.to_all_peace),
                )

            # 猎人和狼王开枪（在死讯公布后）
            shot_player, shot_player2 = None, None

            # Hunter's turn
            for agent in gun_players:
                # If killed and not by witch's poison
                if (
                        killed_player == agent.name
                        and poisoned_player != agent.name
                ):
                    if NAME_TO_ROLE[agent.name] == "hunter":
                        shot_player = await hunter_stage(agent, moderator, current_alive)
                    elif NAME_TO_ROLE[agent.name] == "wolf_king":
                        shot_player = await wolf_king_stage(agent, moderator, current_alive)

            if shot_player:

                # 先更新死亡玩家列表
                werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                    [shot_player], werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive,
                    full_werewolves)

                # 检查胜利条件
                res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                if res:
                    await moderator(res)
                    return

                # Hunter's turn
                for agent in gun_players:
                    # If killed and not by witch's poison
                    if (
                            shot_player == agent.name
                            and poisoned_player != agent.name
                    ):
                        if NAME_TO_ROLE[agent.name] == "hunter":
                            shot_player2 = await hunter_stage(agent, moderator, current_alive)
                        elif NAME_TO_ROLE[agent.name] == "wolf_king":
                            shot_player2 = await wolf_king_stage(agent, moderator, current_alive)

                if shot_player2:
                    werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                        [shot_player2], werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive,
                        full_werewolves)

                    # 检查胜利条件
                    res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                    if res:
                        await moderator(res)
                        return

            # 处理连环枪的遗言（首夜）
            if round_num == 0:
                if shot_player:
                    await handle_last_words(all_players_hub, [shot_player], moderator, all_players_original_order,
                                            is_gunned=True)
                if shot_player2:
                    await handle_last_words(all_players_hub, [shot_player2], moderator, all_players_original_order,
                                            is_gunned=True)

            # 更新死亡玩家列表（包括开枪）
            dead_tonight_with_shots = [killed_player, poisoned_player, shot_player, shot_player2]
            dead_tonight_with_shots = [player for player in dead_tonight_with_shots if player is not None]

            # 如果有新的死亡，更新玩家列表
            if dead_tonight_with_shots != dead_tonight:

                # 检查是否有警长死亡，如果有则处理警徽传递
                dead_sheriff = None
                if sheriff and sheriff in dead_tonight_with_shots:
                    dead_sheriff = sheriff
                    sheriff, sheriff_has_badge = await handle_sheriff_death(dead_sheriff, all_players_hub,
                                                                            current_alive, sheriff,
                                                                            sheriff_has_badge,
                                                                            moderator, all_players_original_order)

            # Discussion
            # 重新排列发言顺序（使用包含开枪的死亡列表）
            dead_tonight = dead_tonight_with_shots
            if exploded_agent and dead_today:
                # 如果之前有人自爆，直接进入黑夜
                # 检查是否有警长死亡，如果有则处理警徽传递
                dead_sheriff = None
                if sheriff and sheriff in dead_today:
                    dead_sheriff = sheriff
                    sheriff, sheriff_has_badge = await handle_sheriff_death(dead_sheriff, all_players_hub,
                                                                            current_alive, sheriff,
                                                                            sheriff_has_badge, moderator,
                                                                            all_players_original_order)

                continue
            if round_num == 0:
                # 首日：警长竞选后的发言顺序
                if sheriff:
                    # 有警长时，从警长的一侧开始发言，警长在归票位
                    speaking_order = get_sheriff_based_speaking_order(current_alive, sheriff,
                                                                      sheriff_speaking_direction)
                elif not dead_tonight:
                    # 平安夜时，基于时间决定发言顺序
                    speaking_order = get_time_based_speaking_order(current_alive)
                else:
                    # 有死亡时，使用原有的逻辑
                    speaking_order = reorder_speaking_order(
                        current_alive,
                        all_players_original_order,
                        dead_tonight,
                        SPEAKING_ORDER_TYPE
                    )
            else:
                # 非首日：新的发言顺序逻辑
                if dead_tonight:
                    # 有死亡时
                    if sheriff:
                        # 有警长，由警长决定从死左还是死右开始
                        sheriff_speaking_direction = await choose_sheriff_speaking_order_for_discussion(
                            all_players_hub, sheriff, True, moderator, sheriff_speaking_direction, current_alive)
                        speaking_order = get_death_based_speaking_order(current_alive, dead_tonight,
                                                                        sheriff_speaking_direction,
                                                                        all_players_original_order)
                    else:
                        # 没有警长，随机决定
                        import random
                        is_clockwise = random.choice([True, False])
                        direction = "死右" if is_clockwise else "死左"
                        await all_players_hub.broadcast(
                            await moderator(f"随机选择{direction}发言顺序。"),
                        )
                        speaking_order = get_death_based_speaking_order(current_alive, dead_tonight, is_clockwise,
                                                                        all_players_original_order)
                else:
                    # 平安夜时
                    if sheriff:
                        # 有警长，由警长决定从警左还是警右开始
                        sheriff_speaking_direction = await choose_sheriff_speaking_order_for_discussion(
                            all_players_hub, sheriff, False, moderator, sheriff_speaking_direction, current_alive)
                        speaking_order = get_sheriff_based_speaking_order(current_alive, sheriff,
                                                                          sheriff_speaking_direction)
                    else:
                        # 没有警长，基于时间决定
                        speaking_order = get_time_based_speaking_order(current_alive)

            await all_players_hub.broadcast(
                await moderator(
                    Prompts.to_all_discuss.format(
                        names=names_to_str(speaking_order),
                    ),
                ),
            )
            # Open the auto broadcast to enable discussion
            all_players_hub.set_auto_broadcast(True)

            # 在讨论过程中检查自爆
            exploded_agent = None
            for agent in speaking_order:
                # 检查是否自爆
                if await handle_self_explode(agent, all_players_hub, moderator, NAME_TO_ROLE):
                    exploded_agent = agent
                    break

                # 正常发言
                await agent(
                    await moderator(f"{agent.name}，请发言。"),
                )

            # 如果有人自爆，直接进入黑夜
            if exploded_agent:
                # 处理自爆玩家的死亡
                dead_today = [exploded_agent.name]

                werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                    dead_today, werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive,
                    full_werewolves)
                # 检查胜利条件
                res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                if res:
                    await moderator(res)
                    return

                # 检查是否有警长死亡，如果有则处理警徽传递
                dead_sheriff = None
                if sheriff and sheriff in dead_today:
                    dead_sheriff = sheriff
                    sheriff, sheriff_has_badge = await handle_sheriff_death(dead_sheriff, all_players_hub,
                                                                            current_alive, sheriff,
                                                                            sheriff_has_badge, moderator,
                                                                            all_players_original_order)

                # 直接进入下一轮（黑夜）
                continue

            # 警长归票发言
            if sheriff:
                sheriff_agent = None
                for agent in current_alive:
                    if agent.name == sheriff:
                        sheriff_agent = agent
                        break

                if sheriff_agent:
                    await all_players_hub.broadcast(
                        await moderator(f"现在请警长{sheriff}进行归票发言。"),
                    )

                    # 检查警长是否自爆
                    if await handle_self_explode(sheriff_agent, all_players_hub, moderator, NAME_TO_ROLE):
                        # 警长自爆了，直接进入黑夜
                        dead_today = [sheriff_agent.name]

                        werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                            dead_today, werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive,
                            full_werewolves)

                        # 检查胜利条件
                        res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                        if res:
                            await moderator(res)
                            return

                        # 处理警徽传递
                        sheriff, sheriff_has_badge = await handle_sheriff_death(dead_sheriff, all_players_hub,
                                                                                current_alive, sheriff,
                                                                                sheriff_has_badge, moderator,
                                                                                all_players_original_order)

                        # 直接进入下一轮（黑夜）
                        continue

                    await sheriff_agent(
                        await moderator(f"{sheriff}，作为警长，请进行归票发言，总结讨论并给出投票建议。"),
                    )

            # 自爆检查
            exploded_agent = None
            for agent in current_alive:
                # 检查是否自爆
                if await handle_self_explode(agent, all_players_hub, moderator, NAME_TO_ROLE):
                    exploded_agent = agent
                    break

            # 如果有人自爆，直接进入黑夜
            if exploded_agent:
                # 处理自爆玩家的死亡
                dead_today = [exploded_agent.name]

                werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                    dead_today, werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive,
                    full_werewolves)

                # 检查胜利条件
                res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                if res:
                    await moderator(res)
                    return

                # 检查是否有警长死亡，如果有则处理警徽传递
                dead_sheriff = None
                if sheriff and sheriff in dead_today:
                    dead_sheriff = sheriff
                    sheriff, sheriff_has_badge = await handle_sheriff_death(dead_sheriff, all_players_hub,
                                                                            current_alive, sheriff,
                                                                            sheriff_has_badge, moderator,
                                                                            all_players_original_order)

                continue

            # Disable auto broadcast to avoid leaking info
            all_players_hub.set_auto_broadcast(False)

            await all_players_hub.broadcast(
                await moderator(Prompts.to_all_vote.format(names_to_str(current_alive))),
            )

            # Voting
            msgs_vote = await fanout_pipeline(
                current_alive,
                await moderator(
                    Prompts.to_all_vote.format(names_to_str(current_alive)),
                ),
                structured_model=get_vote_model(current_alive),
                enable_gather=False,
            )
            # 白天投票使用详细模式，考虑警长权重
            votes_list = [_.metadata.get("vote") for _ in msgs_vote]
            voters_list = [_.name for _ in msgs_vote]
            voted_player, votes, is_tie = majority_vote_with_sheriff(
                votes_list, voters_list, sheriff=sheriff, detailed=True
            )

            # 处理投票结果
            if voted_player is None:
                # 所有人弃票的情况
                result_msg = await moderator(f"投票结果是{votes}。没有人被投票出局。")
                # await all_players_hub.broadcast(
                #     [
                #         *msgs_vote,
                #         result_msg,
                #     ],
                # )
                await all_players_hub.broadcast(result_msg)
            elif is_tie:
                # 平票情况，进入PK环节
                # await all_players_hub.broadcast(msgs_vote)
                # Open the auto broadcast to enable discussion
                all_players_hub.set_auto_broadcast(True)

                # 宣布平票，进入PK环节
                tied_names = names_to_str(voted_player)
                await all_players_hub.broadcast(
                    await moderator(Prompts.to_all_tie.format(votes, tied_names)),
                )

                # PK发言环节
                await all_players_hub.broadcast(
                    await moderator(Prompts.to_all_pk.format(tied_names)),
                )

                # 让平票的玩家依次发言
                pk_agents = [agent for agent in current_alive if agent.name in voted_player]

                # 在PK发言过程中检查自爆
                exploded_agent = None
                for agent in pk_agents:
                    # 检查是否自爆
                    if await handle_self_explode(agent, all_players_hub, moderator, NAME_TO_ROLE):
                        exploded_agent = agent
                        break

                    # 正常发言
                    await agent(
                        await moderator(f"{agent.name}，请发言。"),
                    )

                # 如果有人自爆，直接返回None（表示PK环节被中断）
                if exploded_agent:
                    dead_today = [exploded_agent.name]
                    werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                        dead_today, werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive,
                        full_werewolves)

                    # 检查胜利条件
                    res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                    if res:
                        await moderator(res)
                        return

                    continue

                # PK投票
                await all_players_hub.broadcast(
                    await moderator(Prompts.to_all_pk_vote.format(tied_names)),
                )

                # 创建PK投票模型（只包含平票的玩家）
                pk_agents = [agent for agent in current_alive if agent.name in voted_player]
                # PK投票时，只有非PK台的玩家可以投票
                voting_agents = [agent for agent in current_alive if agent.name not in voted_player]

                # 投票前检查自爆
                for agent in voting_agents:
                    # 检查是否自爆
                    if await handle_self_explode(agent, all_players_hub, moderator, NAME_TO_ROLE):
                        exploded_agent = agent
                        break

                # 如果有人自爆，直接返回None（表示PK环节被中断）
                if exploded_agent:
                    dead_today = [exploded_agent.name]
                    werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                        dead_today, werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive,
                        full_werewolves)

                    # 检查胜利条件
                    res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                    if res:
                        await moderator(res)
                        return

                    continue

                # Disable auto broadcast to avoid leaking info
                all_players_hub.set_auto_broadcast(False)

                await all_players_hub.broadcast(
                    await moderator(Prompts.to_all_pk_vote.format(tied_names)),
                )

                msgs_pk_vote = await fanout_pipeline(
                    voting_agents,  # 只有非PK台的玩家可以投票
                    await moderator(Prompts.to_all_pk_vote.format(tied_names)),
                    structured_model=get_vote_model(pk_agents),
                    enable_gather=False,
                )

                # 处理PK投票结果
                pk_votes_list = [_.metadata.get("vote") for _ in msgs_pk_vote]
                pk_voters_list = [_.name for _ in msgs_pk_vote]
                pk_result, pk_votes, is_tie = majority_vote(
                    pk_votes_list, detailed=True, voters=pk_voters_list
                )

                # await all_players_hub.broadcast(msgs_pk_vote)

                if is_tie:
                    # PK也平票，平安日
                    await all_players_hub.broadcast(
                        await moderator(Prompts.to_all_pk_tie.format(pk_votes, tied_names)),
                    )
                else:
                    # PK有结果
                    await all_players_hub.broadcast(
                        await moderator(Prompts.to_all_pk_res.format(pk_votes, pk_result)),
                    )

                if exploded_agent:
                    dead_sheriff = None
                    if sheriff and sheriff in dead_today:
                        dead_sheriff = sheriff
                        sheriff, sheriff_has_badge = await handle_sheriff_death(dead_sheriff, all_players_hub,
                                                                                current_alive, sheriff,
                                                                                sheriff_has_badge, moderator,
                                                                                all_players_original_order)

                    continue
                if pk_result is None:
                    continue

                voted_player = pk_result
            else:
                # 有明确结果
                result_msg = await moderator(
                    Prompts.to_all_res.format(votes, voted_player),
                )
                # await all_players_hub.broadcast(
                #     [
                #         *msgs_vote,
                #         result_msg,
                #     ],
                # )
                await all_players_hub.broadcast(result_msg)

            shot_player = None
            # 只有当有人被投票出局时才处理猎人/狼王的技能（开枪在遗言前）
            if voted_player is not None:

                werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                    [voted_player], werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive,
                    full_werewolves)

                # 先处理警徽
                # dead_sheriff = None
                # if sheriff and sheriff == voted_player:
                #     dead_sheriff = sheriff
                #     await handle_sheriff_death(dead_sheriff, all_players_hub, current_alive)

                # 检查胜利条件
                res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                if res:
                    await moderator(res)
                    return

                for agent in gun_players:
                    if voted_player == agent.name and NAME_TO_ROLE[agent.name] == "hunter":
                        shot_player = await hunter_stage(agent, moderator, current_alive)
                        if shot_player:
                            await all_players_hub.broadcast(
                                await moderator(
                                    Prompts.to_all_shoot.format(
                                        agent.name,
                                        shot_player,
                                    ),
                                ),
                            )
                    elif voted_player == agent.name and NAME_TO_ROLE[agent.name] == "wolf_king":
                        shot_player = await wolf_king_stage(agent, moderator, current_alive)
                        if shot_player:
                            await all_players_hub.broadcast(
                                await moderator(
                                    Prompts.to_all_shoot.format(
                                        agent.name,
                                        shot_player,
                                    ),
                                ),
                            )

            shot_player2 = None
            # 只有当shot_player不为None时才处理连锁反应
            if shot_player is not None:
                werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                    [shot_player], werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive,
                    full_werewolves)

                # 先处理警徽
                # dead_sheriff = None
                # if sheriff and sheriff == shot_player:
                #     dead_sheriff = sheriff
                #     await handle_sheriff_death(dead_sheriff, all_players_hub, current_alive)

                # 检查胜利条件
                res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                if res:
                    await moderator(res)
                    return

                for agent in gun_players:
                    if shot_player == agent.name and NAME_TO_ROLE[agent.name] == "hunter":
                        shot_player2 = await hunter_stage(agent, moderator, current_alive)
                        if shot_player2:
                            await all_players_hub.broadcast(
                                await moderator(
                                    Prompts.to_all_shoot.format(
                                        agent.name,
                                        shot_player2,
                                    ),
                                ),
                            )
                    elif shot_player == agent.name and NAME_TO_ROLE[agent.name] == "wolf_king":
                        shot_player2 = await wolf_king_stage(agent, moderator, current_alive)
                        if shot_player2:
                            await all_players_hub.broadcast(
                                await moderator(
                                    Prompts.to_all_shoot.format(
                                        agent.name,
                                        shot_player2,
                                    ),
                                ),
                            )
            if shot_player2:
                werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive, full_werewolves = update_players(
                    [shot_player2], werewolves, villagers, seer, hunter, witch, guard, wolf_king, current_alive,
                    full_werewolves)
                # 先处理警徽
                # dead_sheriff = None
                # if sheriff and sheriff == shot_player2:
                #     dead_sheriff = sheriff
                #     await handle_sheriff_death(dead_sheriff, all_players_hub, current_alive)

                # 检查胜利条件
                res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
                if res:
                    await moderator(res)
                    return

            # 处理白天投票出局的遗言（每天被投票出局的玩家都有遗言，开枪在遗言前）
            if voted_player is not None:
                await handle_last_words(all_players_hub, [voted_player], moderator, all_players_original_order,
                                        is_voted=True)

            if shot_player is not None:
                await handle_last_words(all_players_hub, [shot_player], moderator, all_players_original_order,
                                        is_gunned=True)

            if shot_player2 is not None:
                await handle_last_words(all_players_hub, [shot_player2], moderator, all_players_original_order,
                                        is_gunned=True)

            dead_today = [player for player in [voted_player, shot_player, shot_player2] if player is not None]
            if dead_today:
                # 检查是否有警长死亡，如果有则处理警徽传递
                dead_sheriff = None
                if sheriff and sheriff in dead_today:
                    dead_sheriff = sheriff
                    sheriff, sheriff_has_badge = await handle_sheriff_death(dead_sheriff, all_players_hub,
                                                                            current_alive, sheriff,
                                                                            sheriff_has_badge, moderator,
                                                                            all_players_original_order)

            # Check winning again
            res, wolf_win_flag = check_winning(current_alive, full_werewolves, NAME_TO_ROLE)
            if res:
                await moderator(res)
                return


if __name__ == "__main__":
    asyncio.run(main())
