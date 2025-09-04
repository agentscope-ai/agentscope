# -*- coding: utf-8 -*-
"""
.. _routing:

Routing
==========================
在 AgentScope 中有两种实现 Routing 的方法，都简单易实现：

- 使用结构化输出的显式 routing
- 使用工具调用的隐式 routing

.. tip:: 考虑到智能体 routing 没有统一的标准/定义，我们遵循 `Building effective agents <https://www.anthropic.com/engineering/building-effective-agents>`_ 中的设置

显式 Routing
~~~~~~~~~~~~~~~~~~~~~~~~~~
在显式 routing 中，我们可以直接使用智能体的结构化输出来确定将消息路由到哪个智能体。

初始化 routing 智能体
"""
import asyncio
import json
import os
from typing import Literal

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit, ToolResponse

router = ReActAgent(
    name="Router",
    sys_prompt="你是一个路由智能体。你的目标是将用户查询路由到正确的后续任务，注意你不需要回答用户的问题。",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        stream=False,
    ),
    formatter=DashScopeChatFormatter(),
)


# 使用结构化输出指定路由任务
class RoutingChoice(BaseModel):
    your_choice: Literal[
        "Content Generation",
        "Programming",
        "Information Retrieval",
        None,
    ] = Field(
        description="选择正确的后续任务，如果任务太简单或没有合适的任务，则选择 ``None``",
    )
    task_description: str | None = Field(
        description="任务描述",
        default=None,
    )


async def example_router_explicit() -> None:
    """使用结构化输出进行显式路由的示例。"""
    msg_user = Msg(
        "user",
        "帮我写一首诗",
        "user",
    )

    # 路由查询
    msg_res = await router(
        msg_user,
        structured_model=RoutingChoice,
    )

    # 结构化输出存储在 metadata 字段中
    print("结构化输出：")
    print(json.dumps(msg_res.metadata, indent=4, ensure_ascii=False))


asyncio.run(example_router_explicit())

# %%
# 隐式 Routing
# ~~~~~~~~~~~~~~~~~~~~~~~~~
# 另一种方法是将下游智能体包装成工具函数，这样路由智能体就可以根据用户查询决定调用哪个工具。
#
# 我们首先定义几个工具函数：
#


async def generate_python(demand: str) -> ToolResponse:
    """根据需求生成 Python 代码。

    Args:
        demand (``str``):
            对 Python 代码的需求。
    """
    # 示例需求智能体
    python_agent = ReActAgent(
        name="PythonAgent",
        sys_prompt="你是一个 Python 专家，你的目标是根据需求生成 Python 代码。",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            stream=False,
        ),
        memory=InMemoryMemory(),
        formatter=DashScopeChatFormatter(),
        toolkit=Toolkit(),
    )
    msg_res = await python_agent(Msg("user", demand, "user"))

    return ToolResponse(
        content=msg_res.get_content_blocks("text"),
    )


# 为演示目的模拟一些其他工具函数
async def generate_poem(demand: str) -> ToolResponse:
    """根据需求生成诗歌。

    Args:
        demand (``str``):
            对诗歌的需求。
    """
    pass


async def web_search(query: str) -> ToolResponse:
    """在网络上搜索查询。

    Args:
        query (``str``):
            要搜索的查询。
    """
    pass


# %%
# 之后，我们定义一个路由智能体并为其配备上述工具函数。
#

toolkit = Toolkit()
toolkit.register_tool_function(generate_python)
toolkit.register_tool_function(generate_poem)
toolkit.register_tool_function(web_search)

# 使用工具模块初始化路由智能体
router_implicit = ReActAgent(
    name="Router",
    sys_prompt="你是一个路由智能体。你的目标是将用户查询路由到正确的后续任务。",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        stream=False,
    ),
    formatter=DashScopeChatFormatter(),
    toolkit=toolkit,
    memory=InMemoryMemory(),
)


async def example_router_implicit() -> None:
    """使用工具调用进行隐式路由的示例。"""
    msg_user = Msg(
        "user",
        "帮我在 Python 中生成一个快速排序函数",
        "user",
    )

    # 路由查询
    await router_implicit(msg_user)


asyncio.run(example_router_implicit())
