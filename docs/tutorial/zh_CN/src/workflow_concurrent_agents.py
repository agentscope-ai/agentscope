# -*- coding: utf-8 -*-
"""

Concurrent Agents
===================================
在异步编程的帮助下，多智能体并发可以通过 Python 中的 ``asyncio.gather`` 执行。

下面展示了一个简单的示例，其中创建了两个智能体并并发执行。
"""
import asyncio
from datetime import datetime
from typing import Any

from agentscope.agent import AgentBase


class ExampleAgent(AgentBase):
    """用于并发执行的示例智能体。"""

    def __init__(self, name: str) -> None:
        """使用智能体名称初始化智能体。"""
        super().__init__()
        self.name = name

    async def reply(self, *args: Any, **kwargs: Any) -> None:
        """回复消息。"""
        start_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"{self.name} 开始于 {start_time}")
        await asyncio.sleep(3)  # 模拟长时间运行的任务
        end_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"{self.name} 结束于 {end_time}")


async def run_concurrent_agents() -> None:
    """运行并发智能体。"""
    agent1 = ExampleAgent("智能体 1")
    agent2 = ExampleAgent("智能体 2")

    await asyncio.gather(agent1(), agent2())


asyncio.run(run_concurrent_agents())
