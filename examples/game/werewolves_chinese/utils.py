# -*- coding: utf-8 -*-
"""Utility functions for the werewolf game."""
from typing import Any

import numpy as np

from prompt import Prompts

from agentscope.message import Msg
from agentscope.agent import ReActAgent, AgentBase
import random

MAX_GAME_ROUND = 30
MAX_DISCUSSION_ROUND = 3

# candidate_names = [
#     "Batman",
#     "Superman",
#     "Joker",
#     "Luoji",
#     "Turing",
#     "Einstein",
#     "Newton",
#     "Musk",
#     "Jarvis",
#     "Friday",
#     "Spiderman",
#     "Captain",
#     "Harry",
#     "Hermione",
#     "Ron",
#     "Gandalf",
#     "Voldemort",
#     "Frodo",
#     "Aragorn",
#     "Legolas",
#     "Geralt",
#     "Yennefer",
#     "Triss",
#     "Ciri",
#     "Yeye",
#     "Yaojing",
#     "Dawa",
#     "Erwa",
#     "Sanwa",
#     "Siwa",
#     "Wuwa",
#     "Wukong",
#     "Bajie",
#     "Shaseng",
#     "Sanzang",
# ]

candidate_names = ["1号", "2号", "3号", "4号", "5号", "6号", "7号", "8号", "9号", "10号","11号","12号"]

def get_player_name() -> str:
    """Generate player name."""
    return candidate_names.pop(np.random.randint(len(candidate_names)))


def check_winning(
    alive_agents: list,
    wolf_agents: list,
    name_to_role: dict,
) -> str | None:
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
        )
    
    # 好人阵营胜利条件：所有狼人（包括狼王）死亡
    if not wolf_agents:
        return Prompts.to_all_village_win
    
    return None


def get_detailed_vote_info(voters: list[str], votes: list[str]) -> str:
    """生成详细的投票信息：几号投给几号 + 每人得票统计"""
    from collections import Counter
    
    # 生成投票详情
    vote_details = []
    for voter, vote in zip(voters, votes):
        if vote == "弃票":
            vote_details.append(f"{voter}弃票")
        elif vote == "空刀":
            vote_details.append(f"{voter}空刀")
        else:
            vote_details.append(f"{voter}→{vote}")
    
    # 统计得票情况
    valid_votes = [vote for vote in votes if vote not in ["弃票", "空刀"]]
    if valid_votes:
        vote_counts = Counter(valid_votes)
        sorted_votes = sorted(vote_counts.items(), key=lambda x: x[1], reverse=True)
        
        # 添加得票统计
        vote_summary = []
        for player, count in sorted_votes:
            vote_summary.append(f"{player}: {count}票")
        
        # 添加弃票和空刀统计
        all_vote_counts = Counter(votes)
        if "弃票" in all_vote_counts:
            vote_summary.append(f"弃票: {all_vote_counts['弃票']}票")
        if "空刀" in all_vote_counts:
            vote_summary.append(f"空刀: {all_vote_counts['空刀']}票")
        
        return "，".join(vote_details) + " | 得票统计: " + "，".join(vote_summary)
    else:
        return "，".join(vote_details)


def majority_vote(votes: list[str], detailed: bool = False, voters: list[str] = None) -> tuple:
    """Return the vote with the most counts."""
    from collections import Counter
    
    # 统计所有投票（包括弃票和空刀）
    all_vote_counts = Counter(votes)
    
    # 过滤掉弃票和空刀
    valid_votes = [vote for vote in votes if vote not in ["弃票", "空刀"]]
    
    if not valid_votes:
        # 检查是否所有人都是空刀
        if all(vote == "空刀" for vote in votes):
            if detailed and voters:
                conditions = get_detailed_vote_info(voters, votes)
            else:
                conditions = "所有人空刀"
            return None, conditions, False
        else:
            if detailed and voters:
                conditions = get_detailed_vote_info(voters, votes)
            else:
                conditions = "所有人弃票"
            return None, conditions, False
    
    # 统计有效投票
    vote_counts = Counter(valid_votes)
    
    # 获取最高票数
    max_count = max(vote_counts.values())
    
    # 检查是否有平票（多个玩家得票数相同且为最高）
    tied_players = [player for player, count in vote_counts.items() if count == max_count]
    
    # 生成计票信息
    if detailed and voters:
        # 详细模式：显示几号投给几号
        conditions = get_detailed_vote_info(voters, votes)
    else:
        # 简单模式：只显示统计结果
        vote_details = []
        
        # 按票数从高到低排序
        sorted_votes = sorted(vote_counts.items(), key=lambda x: x[1], reverse=True)
        
        for player, count in sorted_votes:
            vote_details.append(f"{player}: {count}票")
        
        # 添加弃票和空刀统计
        if "弃票" in all_vote_counts:
            vote_details.append(f"弃票: {all_vote_counts['弃票']}票")
        if "空刀" in all_vote_counts:
            vote_details.append(f"空刀: {all_vote_counts['空刀']}票")
        
        conditions = "，".join(vote_details)
    
    if len(tied_players) > 1:
        # 平票情况
        return tied_players, conditions, True  # 返回平票玩家列表和是否平票标志
    else:
        # 有明确结果
        result = tied_players[0]
        return result, conditions, False


def majority_vote_with_sheriff(votes: list[str], voters: list[str], sheriff: str = None, detailed: bool = False) -> tuple:
    """带警长权重的投票统计"""
    from collections import Counter
    
    # 统计所有投票（包括弃票和空刀）
    all_vote_counts = Counter(votes)
    
    # 过滤掉弃票和空刀
    valid_votes = [vote for vote in votes if vote not in ["弃票", "空刀"]]
    
    if not valid_votes:
        # 检查是否所有人都是空刀
        if all(vote == "空刀" for vote in votes):
            if detailed:
                conditions = get_detailed_vote_info(voters, votes)
            else:
                conditions = "所有人空刀"
            return None, conditions, False
        else:
            if detailed:
                conditions = get_detailed_vote_info(voters, votes)
            else:
                conditions = "所有人弃票"
            return None, conditions, False
    
    # 统计有效投票，考虑警长权重
    vote_counts = Counter()
    for voter, vote in zip(voters, valid_votes):
        if voter == sheriff:
            # 警长有1.5票权重
            vote_counts[vote] += 1.5
        else:
            vote_counts[vote] += 1
    
    # 获取最高票数
    max_count = max(vote_counts.values())
    
    # 检查是否有平票（多个玩家得票数相同且为最高）
    tied_players = [player for player, count in vote_counts.items() if count == max_count]
    
    # 生成计票信息
    if detailed:
        # 详细模式：显示几号投给几号 + 得票统计
        conditions = get_detailed_vote_info(voters, votes)
        
        # 添加权重信息
        if sheriff and sheriff in voters:
            conditions += f" | 警长{sheriff}拥有1.5票权重"
    else:
        # 简单模式：只显示统计结果
        vote_details = []
        
        # 按票数从高到低排序
        sorted_votes = sorted(vote_counts.items(), key=lambda x: x[1], reverse=True)
        
        for player, count in sorted_votes:
            if count == int(count):
                vote_details.append(f"{player}: {int(count)}票")
            else:
                vote_details.append(f"{player}: {count}票")
        
        # 添加弃票和空刀统计
        if "弃票" in all_vote_counts:
            vote_details.append(f"弃票: {all_vote_counts['弃票']}票")
        if "空刀" in all_vote_counts:
            vote_details.append(f"空刀: {all_vote_counts['空刀']}票")
        
        conditions = "，".join(vote_details)
    
    if len(tied_players) > 1:
        # 平票情况
        return tied_players, conditions, True  # 返回平票玩家列表和是否平票标志
    else:
        # 有明确结果
        result = tied_players[0]
        return result, conditions, False


def handle_sheriff_election_tie(tied_candidates: list[str], votes: list[str], voters: list[str]) -> tuple:
    """处理警长竞选平票情况"""
    from collections import Counter
    
    # 生成详细投票信息
    vote_details = []
    for voter, vote in zip(voters, votes):
        if vote == "弃票":
            vote_details.append(f"{voter}弃票")
        else:
            vote_details.append(f"{voter}→{vote}")
    
    # 统计得票情况
    valid_votes = [vote for vote in votes if vote != "弃票"]
    if valid_votes:
        vote_counts = Counter(valid_votes)
        sorted_votes = sorted(vote_counts.items(), key=lambda x: x[1], reverse=True)
        
        vote_summary = []
        for player, count in sorted_votes:
            vote_summary.append(f"{player}: {count}票")
        
        if "弃票" in Counter(votes):
            vote_summary.append(f"弃票: {Counter(votes)['弃票']}票")
        
        conditions = "，".join(vote_details) + " | 得票统计: " + "，".join(vote_summary)
    else:
        conditions = "，".join(vote_details)
    
    return tied_candidates, conditions, True


def get_time_based_speaking_order(agents: list[ReActAgent]) -> list[ReActAgent]:
    """基于当前时间决定发言顺序，找到与时间最相近的玩家号码开始转"""
    import datetime
    
    current_time = datetime.datetime.now()
    minute = current_time.minute
    
    # 将分钟数映射到玩家号码（1-12）
    # 分钟数0-59映射到玩家号码1-12
    target_player_num = (minute % 12) + 1
    
    # 找到最接近目标号码的玩家
    # 玩家名字格式是 "1号", "2号", "3号" 等
    def extract_player_number(agent_name):
        """从玩家名字中提取号码"""
        import re
        # 匹配 "数字号" 格式
        match = re.search(r'(\d+)号', agent_name)
        if match:
            return int(match.group(1))
        return 1  # 默认返回1
    
    # 计算每个玩家与目标号码的距离
    player_distances = []
    for agent in agents:
        player_num = extract_player_number(agent.name)
        # 计算环形距离（考虑12个玩家的环形排列）
        distance = min(abs(player_num - target_player_num), 
                      12 - abs(player_num - target_player_num))
        player_distances.append((distance, player_num, agent))
    
    # 找到距离最小的玩家
    min_distance = min(player_distances, key=lambda x: x[0])[0]
    closest_players = [item for item in player_distances if item[0] == min_distance]
    
    # 如果有多个玩家距离相同，选择号码最小的
    start_player = min(closest_players, key=lambda x: x[1])[2]
    
    # 找到起始玩家在列表中的位置
    start_index = agents.index(start_player)
    
    # 从起始玩家开始重新排列
    reordered = agents[start_index:] + agents[:start_index]
    if random.random() < 0.5:
        reordered = reordered[::-1]

    return reordered


def get_death_based_speaking_order(agents: list[ReActAgent], dead_players: list[str], is_clockwise: bool = True, all_agents: list[ReActAgent] = None) -> list[ReActAgent]:
    """基于死亡玩家位置决定发言顺序"""
    if not dead_players:
        return agents.copy()
    
    # 如果没有提供all_agents，使用agents（向后兼容）
    if all_agents is None:
        all_agents = agents
    
    # 找到第一个死亡玩家的位置（在all_agents中寻找）
    death_index = -1
    for i, agent in enumerate(all_agents):
        if agent.name in dead_players:
            death_index = i
            break
    
    if death_index == -1:
        return agents.copy()
    
    # 基于死亡玩家位置重新排列存活的玩家
    if is_clockwise:
        # 顺时针：从死者右边开始发言
        start_pos = (death_index + 1) % len(all_agents)
    else:
        # 逆时针：从死者左边开始发言
        start_pos = (death_index - 1) % len(all_agents)
    
    # 从start_pos开始，按原始顺序重新排列存活玩家
    reordered = []
    for i in range(len(all_agents)):
        pos = (start_pos + i) % len(all_agents)
        agent = all_agents[pos]
        # 只添加存活的玩家
        if agent in agents:
            reordered.append(agent)
    
    return reordered


def get_sheriff_based_speaking_order(agents: list[ReActAgent], sheriff: str, is_clockwise: bool = True) -> list[ReActAgent]:
    """基于警长位置决定发言顺序，警长在归票位"""
    if not sheriff:
        return agents.copy()
    
    # 找到警长的位置
    sheriff_index = -1
    for i, agent in enumerate(agents):
        if agent.name == sheriff:
            sheriff_index = i
            break
    
    if sheriff_index == -1:
        return agents.copy()
    
    if is_clockwise:
        # 顺时针：从警长右边开始，警长最后发言（归票位）
        return agents[sheriff_index+1:] + agents[:sheriff_index+1]
    else:
        # 逆时针：从警长左边开始，警长最后发言（归票位）
        return agents[sheriff_index:] + agents[:sheriff_index]


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
    return ", ".join([*names[:-1], "和 " + names[-1]])


def reorder_speaking_order(
    alive_agents: list[ReActAgent], 
    all_agents: list[ReActAgent] = None,
    dead_players: list[str] = None,
    order_type: str = "from_death"
) -> list[ReActAgent]:
    """重新排列发言顺序
    
    Args:
        alive_agents: 当前存活的玩家列表
        all_agents: 所有玩家的原始顺序列表（包括死者），用于计算从死者位置开始
        dead_players: 本轮死亡的玩家列表
        order_type: 排序类型
            - "from_death": 从死者位置开始发言
            - "reverse": 逆序发言
            - "original": 保持原顺序
    
    Returns:
        重新排列后的玩家列表
    """
    if not alive_agents:
        return alive_agents
    
    if order_type == "original":
        return alive_agents
    
    elif order_type == "reverse":
        return alive_agents[::-1]
    
    elif order_type == "from_death" and dead_players and all_agents:
        # 从死者位置开始发言
        # 在所有玩家中找到死者的位置
        dead_positions = []
        for i, agent in enumerate(all_agents):
            if agent.name in dead_players:
                dead_positions.append(i)
        
        if dead_positions:
            # 从最后一个死者的下一个位置开始
            last_dead_pos = max(dead_positions)
            start_pos = (last_dead_pos + 1) % len(all_agents)
            
            # 从start_pos开始，按原始顺序重新排列存活玩家
            reordered = []
            for i in range(len(all_agents)):
                pos = (start_pos + i) % len(all_agents)
                agent = all_agents[pos]
                # 只添加存活的玩家
                if agent in alive_agents:
                    reordered.append(agent)
            if random.random() < 0.5:
                reordered = reordered[::-1]
            return reordered
        else:
            # 如果没有死者，保持原顺序
            return alive_agents
    
    else:
        # 默认保持原顺序
        return alive_agents


class EchoAgent(AgentBase):
    """Echo agent that repeats the input message."""

    def __init__(self) -> None:
        super().__init__()
        self.name = "主持人"

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
