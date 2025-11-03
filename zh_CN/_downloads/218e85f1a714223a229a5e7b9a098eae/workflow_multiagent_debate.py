# -*- coding: utf-8 -*-
"""
.. _multiagent-debate:

Multi-Agent Debate
========================

Multi-Agent debate 模拟不同智能体之间的多轮讨论场景，通常包括几个 solver 和一个 aggregator。
典型情况下，solver 生成并交换他们的答案，而 aggregator 收集并总结答案。

我们实现了 `EMNLP 2024`_ 中的示例，其中两个 solver 智能体将按固定顺序讨论一个话题，根据先前的辩论历史表达他们的论点。
在每一轮中，主持人智能体将决定是否可以在当前轮获得最终的正确答案。
"""
import asyncio
import os

from pydantic import Field, BaseModel

from agentscope.agent import ReActAgent
from agentscope.formatter import (
    DashScopeMultiAgentFormatter,
)
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import MsgHub

# 准备一个话题
topic = "两个圆外切且没有相对滑动。圆A的半径是圆B半径的1/3。圆A绕圆B滚动一圈回到起点。圆A总共会旋转多少次？"


# 创建两个辩论者智能体，Alice 和 Bob，他们将讨论这个话题。
def create_solver_agent(name: str) -> ReActAgent:
    """获取一个解决者智能体。"""
    return ReActAgent(
        name=name,
        sys_prompt=f"你是一个名为 {name} 的辩论者。你好，欢迎来到"
        "辩论比赛。我们的目标是找到正确答案，因此你没有必要完全同意对方"
        f"的观点。辩论话题如下所述：{topic}",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            stream=False,
        ),
        formatter=DashScopeMultiAgentFormatter(),
    )


alice, bob = [create_solver_agent(name) for name in ["Alice", "Bob"]]

# 创建主持人智能体
moderator = ReActAgent(
    name="Aggregator",
    sys_prompt=f"""你是一个主持人。将有两个辩论者参与辩论比赛。他们将就以下话题提出观点并进行讨论：
``````
{topic}
``````
在每轮讨论结束时，你将评估辩论是否结束，以及话题正确的答案。""",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        stream=False,
    ),
    # 使用多智能体格式化器，因为主持人将接收来自多于用户和助手的消息
    formatter=DashScopeMultiAgentFormatter(),
)


# 主持人的结构化输出模型
class JudgeModel(BaseModel):
    """主持人的结构化输出模型。"""

    finished: bool = Field(description="辩论是否结束。")
    correct_answer: str | None = Field(
        description="辩论话题的正确答案，仅当辩论结束时提供该字段。否则保留为 None。",
        default=None,
    )


async def run_multiagent_debate() -> None:
    """运行多智能体辩论工作流。"""
    while True:
        # MsgHub 中参与者的回复消息将广播给所有参与者。
        async with MsgHub(participants=[alice, bob, moderator]):
            await alice(
                Msg(
                    "user",
                    "你是正方，请表达你的观点。",
                    "user",
                ),
            )
            await bob(
                Msg(
                    "user",
                    "你是反方。你不同意正方的观点。请表达你的观点和理由。",
                    "user",
                ),
            )

        # Alice 和 Bob 不需要知道主持人的消息，所以主持人在 MsgHub 外部调用。
        msg_judge = await moderator(
            Msg(
                "user",
                "现在你已经听到了他们的辩论，现在判断辩论是否结束，以及你能得到正确答案吗？",
                "user",
            ),
            structured_model=JudgeModel,
        )

        if msg_judge.metadata.get("finished"):
            print(
                "\n辩论结束，正确答案是：",
                msg_judge.metadata.get("correct_answer"),
            )
            break


asyncio.run(run_multiagent_debate())


# %%
# 进一步阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# - :ref:`pipeline`
#
# .. _EMNLP 2024:
# Encouraging Divergent Thinking in Large Language Models through Multi-Agent Debate. EMNLP 2024.
#
