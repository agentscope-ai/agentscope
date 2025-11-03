# -*- coding: utf-8 -*-
"""
.. _state:

状态/会话管理
=================================

在 AgentScope 中，**"状态"** 是指智能体在运行中某一时刻的数据快照，包括其当前的系统提示、记忆、上下文、装备的工具以及其他 **随时间变化** 的信息。

为了管理应用程序的状态，AgentScope 设计实现了 **自动状态注册** 和 **会话级状态管理**，具有以下特性：

- 支持所有继承自 ``StateModule`` 的变量的 **自动状态注册**
- 支持使用自定义序列化/反序列化方法的 **手动状态注册**
- 支持 **会话/应用程序级别管理**
"""
import asyncio
import json
import os

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.module import StateModule
from agentscope.session import JSONSession
from agentscope.tool import Toolkit

# %%
# 状态模块
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# ``StateModule`` 类是 AgentScope 中状态管理的基础，提供三个基本函数：
#
# .. list-table:: ``StateModule`` 的方法
#     :header-rows: 1
#
#     * - 方法
#       - 参数
#       - 描述
#     * - ``register_state``
#       - | ``attr_name``,
#         | ``custom_to_json`` （可选）,
#         | ``custom_from_json`` （可选）
#       - 将属性注册为其状态，带有可选的序列化/反序列化函数。
#     * - ``state_dict``
#       -
#       - 获取当前对象的状态字典
#     * - ``load_state_dict``
#       - | ``state_dict``,
#         | ``strict`` （可选）
#       - 将状态字典加载到当前对象
#
# 在 ``StateModule`` 的对象中，以下所有属性都将被视为其状态的一部分：
#
# - 继承自 ``StateModule`` 的 **属性**
# - 通过 ``register_state`` 方法注册的 **属性**
#
# 注意 ``StateModule`` 支持 **嵌套** 序列化和反序列化，例如下面的示例中，``ClassB`` 中包含一个 ``ClassA`` 的实例：
#


class ClassA(StateModule):
    def __init__(self) -> None:
        super().__init__()
        self.cnt = 123
        # 将 cnt 属性注册为状态
        self.register_state("cnt")


class ClassB(StateModule):
    def __init__(self) -> None:
        super().__init__()

        # 属性 "a" 继承自 StateModule，将自动视为状态的一部分
        self.a = ClassA()

        # 手动将属性 "c" 注册为状态
        self.c = "Hello, world!"
        self.register_state("c")


obj_b = ClassB()

print("obj_b.a 的状态：")
print(obj_b.a.state_dict())

print("\nobj_b 的状态：")
print(json.dumps(obj_b.state_dict(), indent=4))

# %%
# 我们可以观察到 ``obj_b`` 的状态自动包含了其属性 ``a`` 的状态。
#
# 在 AgentScope 中，``AgentBase``、``MemoryBase``、``LongTermMemoryBase`` 和 ``Toolkit`` 类都继承自 ``StateModule``，因此支持自动和嵌套状态管理。
#

# 创建一个智能体
agent = ReActAgent(
    name="Friday",
    sys_prompt="你是一个名为 Friday 的助手。",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    ),
    formatter=DashScopeChatFormatter(),
    memory=InMemoryMemory(),
    toolkit=Toolkit(),
)

initial_state = agent.state_dict()

print("智能体的初始状态：")
print(json.dumps(initial_state, indent=4, ensure_ascii=False))

# %%
# 然后我们通过生成回复消息来改变其状态：
#


async def example_agent_state() -> None:
    """智能体状态管理示例。"""
    await agent(Msg("user", "你好，智能体！", "user"))

    print("生成回复后智能体的状态：")
    print(json.dumps(agent.state_dict(), indent=4, ensure_ascii=False))


asyncio.run(example_agent_state())

# %%
# 现在我们将智能体的状态恢复到初始状态：
#

agent.load_state_dict(initial_state)

print("加载初始状态后：")
print(json.dumps(agent.state_dict(), indent=4, ensure_ascii=False))

# %%
# 会话管理
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# 会话（Session）是应用程序中状态的集合，例如多个智能体。
#
# AgentScope 提供了 ``SessionBase`` 类，包含两个用于会话管理的抽象方法：``save_session_state`` 和 ``load_session_state``。
# 开发者可以继承该类来实现自己的状态保存方案，例如对接到自己的数据库或文件系统。
#
# 在 AgentScope 中，我们提供了基于 JSON 和文件系统的的会话类 ``JSONSession``，
# 它会将状态保存到会化 ID 命名的 JSON 文件中，也可以从中加载状态。
#
# 保存会话状态
# -----------------------------------------
#

# 通过生成回复消息改变智能体状态
asyncio.run(example_agent_state())

print("\n智能体的状态：")
print(json.dumps(agent.state_dict(), indent=4, ensure_ascii=False))

# %%
# 然后我们将其保存到会话文件中：

session = JSONSession(
    save_dir="./",  # 保存所有session文件的目录
)


async def example_session() -> None:
    """会话管理示例。"""

    # 可以保存多个状态，只需要输入的对象为 `StateModule` 的子类。
    await session.save_session_state(
        session_id="user_bob",
        agent=agent,
    )

    print("保存的状态：")
    with open("./user_bob.json", "r", encoding="utf-8") as f:
        print(json.dumps(json.load(f), indent=4, ensure_ascii=False))


asyncio.run(example_session())

# %%
# 加载会话状态
# -----------------------------------------
# 然后我们可以从会话文件中加载状态：
#


async def example_load_session() -> None:
    """加载会话状态示例。"""

    # 清空智能体状态
    await agent.memory.clear()

    print("当前智能体状态：")
    print(json.dumps(agent.state_dict(), indent=4, ensure_ascii=False))

    # 从会话文件中加载状态
    await session.load_session_state(
        session_id="user_bob",
        # 这里使用的关键词参数必须与 `save_session_state` 中的参数一致
        agent=agent,
    )
    print("加载会话状态后智能体的状态：")
    print(json.dumps(agent.state_dict(), indent=4, ensure_ascii=False))


asyncio.run(example_load_session())

# %%
# 此时我们可以观察到智能体的状态已经恢复到之前保存的状态。
#
