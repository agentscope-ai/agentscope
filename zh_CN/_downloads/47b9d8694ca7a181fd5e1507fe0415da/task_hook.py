# -*- coding: utf-8 -*-
"""
.. _hook:

智能体钩子函数
===========================

钩子（Hook）是 AgentScope 中的扩展点，允许开发者在特定位置自定义智能体行为，提供了一种灵活的方式来修改或扩展智能体的功能，而无需更改其核心实现。

在 AgentScope 中，钩子围绕智能体的核心函数实现：


.. list-table:: AgentScope 中支持的钩子类型
    :header-rows: 1

    * - 智能体类
      - 核心函数
      - 钩子类型
      - 描述
    * - | ``AgentBase`` 及其子类
      - ``reply``
      - | ``pre_reply``
        | ``post_reply``
      - 智能体回复消息前/后的钩子
    * -
      - ``print``
      - | ``pre_print``
        | ``post_print``
      - 向目标输出（如终端、Web 界面）打印消息前/后的钩子
    * -
      - ``observe``
      - | ``pre_observe``
        | ``post_observe``
      - 从环境或其它智能体观察消息前/后的钩子
    * - | ``ReActAgentBase`` 及其子类
      - | ``reply``
        | ``print``
        | ``observe``
      - | ``pre_reply``
        | ``post_reply``
        | ``pre_print``
        | ``post_print``
        | ``pre_observe``
        | ``post_observe``
      -
    * -
      - ``_reasoning``
      - | ``pre_reasoning``
        | ``post_reasoning``
      - 智能体推理过程前/后的钩子
    * -
      - ``_acting``
      - | ``pre_acting``
        | ``post_acting``
      - 智能体行动过程前/后的钩子

.. tip:: 由于 AgentScope 中的钩子函数是通过 meta class 实现的，因此支持继承。

为了简化使用，AgentScope 为所有钩子提供了统一的签名。

"""
import asyncio
from typing import Any, Type

from agentscope.agent import ReActAgentBase, AgentBase
from agentscope.message import Msg


# %%
# 钩子签名
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# AgentScope 为所有前置（pre_）和后置（post_）钩子提供统一的钩子签名，如下所示：
#
# **前置钩子签名**
#
# .. list-table:: 所有前置钩子的签名
#   :header-rows: 1
#
#   * -
#     - 名称
#     - 描述
#   * - 参数
#     - ``self: AgentBase | ReActAgentBase``
#     - 智能体实例
#   * -
#     - ``kwargs: dict[str, Any]``
#     - | 目标函数的输入参数，或来自最近
#       | 一个非 None 返回值的钩子修
#       | 改后的参数
#   * - 返回值
#     - ``dict[str, Any] | None``
#     - 修改后的参数或 None
#
# .. note:: 核心函数的所有位置参数（*args）和关键字参数（**kwargs）被统一成单个 ``kwargs`` 字典传递给钩子函数
#
# 前置钩子模板定义如下：
#


def pre_hook_template(
    self: AgentBase | ReActAgentBase,
    kwargs: dict[str, Any],
) -> dict[str, Any] | None:  # 修改后的输入
    """前置钩子模板。"""
    pass


# %%
# **后置钩子签名**
#
# 对于后置钩子，在签名中增加了一个额外的 ``output`` 参数，表示目标函数的输出。
# 如果核心函数没有输出，``output`` 参数将为 ``None``。
#
# .. list-table:: 所有后置钩子的签名
#   :header-rows: 1
#
#   * -
#     - 名称
#     - 描述
#   * - 参数
#     - ``self: AgentBase | ReActAgentBase``
#     - 智能体实例
#   * -
#     - ``kwargs: dict[str, Any]``
#     - | 包含目标函数所有参数的字典
#   * -
#     - ``output: Any``
#     - | 目标函数的输出或来自前序钩子
#       | 最近一个非 None 返回值
#   * - 返回值
#     - ``dict[str, Any] | None``
#     - 修改后的输出或 None
#


def post_hook_template(
    self: AgentBase | ReActAgentBase,
    kwargs: dict[str, Any],
    output: Any,  # 目标函数的输出
) -> Any:  # 修改后的输出
    """后置钩子模板。"""
    pass


# %%
# 钩子管理
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# AgentScope 提供实例级（instance）和类级（class）钩子，其区别在于钩子函数的作用范围。
# 它们按以下顺序执行：
#
# .. image:: ../../_static/images/sequential_hook.png
#   :width: 90%
#   :align: center
#   :alt: AgentScope 中的钩子
#   :class: bordered-image
#
# AgentScope 提供内置方法来管理实例级和类级的钩子，如下所示：
#
# .. list-table:: AgentScope 中的钩子管理方法
#   :header-rows: 1
#
#   * - 级别
#     - 方法
#     - 描述
#   * - 实例级
#     - ``register_instance_hook``
#     - | 为当前对象注册具有给定钩子类型
#       | 和名称的钩子。
#   * -
#     - ``remove_instance_hook``
#     - | 移除当前对象具有给定钩子类型
#       | 和名称的钩子。
#   * -
#     - ``clear_instance_hooks``
#     - | 清除当前对象具有给定钩子类型
#       | 的所有钩子。
#   * - 类级
#     - ``register_class_hook``
#     - | 为该类的所有对象注册具有给定
#       | 钩子类型和名称的钩子。
#   * -
#     - ``remove_class_hook``
#     - | 移除该类所有对象具有给定
#       | 钩子类型和名称的钩子。
#   * -
#     - ``clear_class_hooks``
#     - | 清除该类所有对象具有给定
#       | 钩子类型的所有钩子。
#
# 使用钩子时，开发者需要注意以下规则：
#
# .. important:: **执行顺序**
#
#  - 钩子按注册顺序执行
#  - 多个钩子可以链式连接
#  **返回值处理**
#
#  - 对于前置钩子：非 None 返回值会传递给下一个钩子或核心函数
#   - 当钩子返回 None 时，下一个钩子将使用前序钩子中最近的非 None 返回值
#   - 如果所有前序钩子都返回 None，那该钩子接收原始参数的副本作为输入
#   - 最后一个非 None 返回值（或如果所有钩子都返回 None 则使用原始参数）传递给核心函数
#  - 对于后置钩子：工作方式与前置钩子相似。
#  **重要提示**：不要在钩子内调用核心函数（reply/speak/observe/_reasoning/_acting）以避免循环调用！
#
# 以下面的智能体为例，我们可以看到如何注册、移除和清除钩子：
#


# 创建一个简单的测试智能体类
class TestAgent(AgentBase):
    """用于演示钩子的测试智能体。"""

    async def reply(self, msg: Msg) -> Msg:
        """回复消息。"""
        return msg


# %%
# 我们创建一个实例级钩子和一个类级钩子来在回复前修改消息内容。
#


# 创建两个前置回复钩子
def instance_pre_reply_hook(
    self: AgentBase,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """修改消息内容的前置回复钩子。"""
    msg = kwargs["msg"]
    msg.content += "[instance-pre-reply]"
    # 返回修改后的 kwargs
    return {
        **kwargs,
        "msg": msg,
    }


def cls_pre_reply_hook(
    self: AgentBase,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """修改消息内容的前置回复钩子。"""
    msg = kwargs["msg"]
    msg.content += "[cls-pre-reply]"
    # 返回修改后的 kwargs
    return {
        **kwargs,
        "msg": msg,
    }


# 注册类钩子
TestAgent.register_class_hook(
    hook_type="pre_reply",
    hook_name="test_pre_reply",
    hook=cls_pre_reply_hook,
)

# 注册实例钩子
agent = TestAgent()
agent.register_instance_hook(
    hook_type="pre_reply",
    hook_name="test_pre_reply",
    hook=instance_pre_reply_hook,
)


async def example_test_hook() -> None:
    """测试钩子的示例函数。"""
    msg = Msg(
        name="user",
        content="Hello, world!",
        role="user",
    )
    res = await agent(msg)
    print("响应内容：", res.content)
    TestAgent.clear_class_hooks()


asyncio.run(example_test_hook())

# %%
# 我们可以看到 "[instance-pre-reply]" 和 "[cls-pre-reply]" 被添加到了消息内容中。
#
