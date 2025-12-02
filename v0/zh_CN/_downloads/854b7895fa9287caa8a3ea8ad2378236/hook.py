# -*- coding: utf-8 -*-
"""
.. _hook:
钩子函数
===========================

钩子函数（Hooks）允许开发人员在特定执行点自定义智能体行为。它们提供了一种灵活的方式来修改或扩展智能体的功能，而无需更改其核心实现。

AgentScope 围绕三个核心智能体函数实现了钩子函数：

- `reply`：根据智能体的当前状态生成响应消息
- `speak`：在终端显示和记录消息
- `observe`：记录传入的消息

类别
-------------
每个核心函数都有对应的前后执行钩子：

- `pre_reply_hook` / `post_reply_hook`
- `pre_speak_hook` / `post_speak_hook`
- `pre_observe_hook` / `post_observe_hook`

例如，您可以使用 `pre_speak_hook` 将消息重定向到不同的输出，如 Web 界面或外部应用程序。

.. important:: 当使用钩子函数时，请记住以下重要规则：

 1. **钩子函数签名**
  - 第一个参数必须是 `AgentBase` 对象，即 `self`
  - 后续参数是原始函数参数的副本
 2. **执行顺序**
  - 钩子按注册顺序执行
  - 多个钩子函数可以链接在一起
 3. **返回值处理**
  - 对于前置钩子：非 None 返回值会传递给下一个钩子或目标函数
   - 当钩子返回 None 时，下一个钩子将使用前面钩子的最近非 None 返回值
   - 如果所有前面的钩子返回 None，则下一个钩子将接收原始参数的副本
   - 最终的非 None 返回值（或者如果所有钩子返回 None，则原始参数）将传递给目标函数
  - 对于后置钩子： 只有 `post-reply` 钩子可以拥有返回值，工作方式与前置钩子相同
 4. **重要提示**：避免在钩子函数中调用目标函数（reply/speak/observe），以避免循环调用

函数签名
-----------------------

我们在下面为每个钩子函数提供了模板，以显示其参数签名。开发者可以将这些模板复制粘贴到您的代码中，
并根据需要进行自定义。
"""

from typing import Union, Tuple, Dict, Any

from agentscope.agents import AgentBase
from agentscope.message import Msg


def pre_reply_hook_template(
    self: AgentBase,
    args: Tuple[Any, ...],  # 位置参数 (positional arguments)
    kwargs: Dict[str, Any],  # 关键词参数 (keyword arguments)
) -> Union[None, Tuple[Tuple[Any, ...], Dict[str, Any]]]:  # 修改后的位置和关键词参数
    """Pre-reply hook template."""
    pass


def post_reply_hook_template(
    self: AgentBase,
    args: Tuple[Any, ...],  # 位置参数 (positional arguments)
    kwargs: Dict[str, Any],  # 关键词参数 (keyword arguments)
    output: Msg,  # 输出消息
) -> Union[None, Msg]:  # 修改后的输出消息
    """Post-reply hook template."""
    pass


def pre_speak_hook_template(
    self: AgentBase,
    x: Msg,  # 需要显示的消息
    stream: bool,  # 是否处于流式输出状态
    last: bool,  # 当前消息是否是流中的最后一个 Chunk
) -> Union[Msg, None]:  # 修改后的消息
    """speak 函数前置钩子函数模版"""
    pass


def post_speak_hook_template(self: AgentBase) -> None:
    """speak 函数后置钩子函数模版"""
    pass


def pre_observe_hook_template(
    self: AgentBase,
    x: Union[Msg, list[Msg]],
) -> Union[None, Union[Msg, list[Msg]]]:  # 修改后的输入消息
    """observe 函数前置钩子函数模版"""
    pass


def post_observe_hook_template(self: AgentBase) -> None:
    """observe 函数后置钩子函数模版"""
    pass


# %%
# 样例
# -------------------
# AgentScope 允许通过调用相应的方法注册、移除和清除钩子函数。
#
# 用户可以将钩子函数注册到 **对象** 和 **类** 两个不同的级别，其中 **对象** 级别的钩子函数只对当前的对象有效，**类** 级别的钩子函数会对改类的所有对象生效。
# 下面我们展示两个不同级别钩子函数的使用方式。
#
# .. note:: 对象级别的钩子函数存储在对象的 `_hooks_{hook_type}` 属性上，类级别的钩子函数存储在类的 `_class_hooks_{hook_type}` 属性上。
#
# .. note:: 对所有位置的钩子函数来说，对象级别和类级别钩子函数的执行顺序为：对象级别的钩子函数 --> 类级别的钩子函数
#
# 对象级别
# ^^^^^^^^^^^^^^^^^^^^^^^
#
# 我们首先创建一个简单的智能体，用于回显传入的消息：

from typing import Optional, Union

from agentscope.agents import AgentBase
from agentscope.message import Msg


class TestAgent(AgentBase):
    def __init__(self):
        super().__init__(name="TestAgent")

    def reply(self, x: Optional[Union[Msg, list[Msg]]] = None) -> Msg:
        return x


# %%
# Reply 钩子函数
# """""""""""""""""
# 接下来，我们定义两个前置钩子函数，它们都修改输入的消息，但一个返回修改后的消息，另一个不返回：


def pre_reply_hook_1(
    self,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Union[None, Tuple[Tuple[Any, ...], Dict[str, Any]]]:
    """第一个前置回复钩子，修改消息内容，但是不进行返回。"""
    if isinstance(args[0], Msg):
        args[0].content = "[Pre-hook-1] " + args[0].content
    elif isinstance(args[0], list):
        for msg in args[0]:
            msg.content = "[Pre-hook-1] " + msg.content
    return None


def pre_reply_hook_2(
    self,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Union[None, Tuple[Tuple[Any, ...], Dict[str, Any]]]:
    """第二个前置回复钩子，用于更改消息内容。"""
    if isinstance(args[0], Msg):
        args[0].content = "[Pre-hook-2] " + args[0].content
    elif isinstance(args[0], list):
        for msg in args[0]:
            msg.content = "[Pre-hook-2] " + msg.content
    return args, kwargs  # 返回修改后的参数


# %%
# 然后，我们创建一个后置钩子，用于将后缀附加到消息内容：


def post_reply_hook(self, args, kwargs, output) -> Msg:
    """后置回复钩子，用于将后缀附加到消息内容。"""
    output.content += " [Post-hook]"
    return output


# %%
# 最后，我们注册钩子并测试智能体：

agent = TestAgent()

agent.register_hook("pre_reply", "pre_hook_1", pre_reply_hook_1)
agent.register_hook("pre_reply", "pre_hook_2", pre_reply_hook_2)
agent.register_hook("post_reply", "post_hook", post_reply_hook)

msg = Msg("user", "[原始消息]", "user")

msg_response = agent(msg)

print("回复消息的 content 域:\n", msg_response.content)

# %%
# 可以看到，响应消息有一个 "[Pre-hook-2]" 前缀和一个 "[Post-hook]" 后缀。
# 添加 "[Pre-hook-1]" 前缀失败，因为第一个前置钩子没有返回值
#
# Speak 钩子函数
# """""""""""""""""
# 为了兼容流式输出，pre-speak 钩子函数接受两个额外的参数：
#
# - `stream`: 一个布尔标志，指示是否处于流式输出
# - `last`: 一个布尔标志，指示当前输入消息是否是流中的最后一个 Chunk
#
# .. tip:: 当处理流式输出时，可以使用消息 id 来确定两个消息是否来自同一个流式输出。
#
# 我们在下面展示如何使用 pre/post-speak 钩子：

from agentscope.agents import DialogAgent
import agentscope

agentscope.init(
    model_configs=[
        {
            "config_name": "streaming_config",
            "model_type": "dashscope_chat",
            "model_name": "qwen-max",
            "stream": True,
        },
    ],
)

streaming_agent = DialogAgent(
    name="TestAgent",
    model_config_name="streaming_config",
    sys_prompt="You're a helpful assistant.",
)


# 创建一个 pre-speak 钩子，用于显示消息内容
def pre_speak_hook(self, x: Msg, stream: bool, last: bool) -> Msg:
    """speak 函数前置钩子函数"""
    # 你可以在这里更改或重定向消息
    print(
        "id: ",
        x.id,
        "stream: ",
        stream,
        "last: ",
        last,
        "content: ",
        x.content,
    )
    # 防止打印消息错乱
    x.content = ""
    return x


def post_speak_hook(self) -> None:
    """speak 函数后置钩子函数"""
    # 在这里计算调用 speak 函数的次数
    if not hasattr(self, "cnt"):
        self.cnt = 0
    self.cnt += 1


# Register the hooks
streaming_agent.register_hook("pre_speak", "pre_speak_hook", pre_speak_hook)
streaming_agent.register_hook("post_speak", "post_speak_hook", post_speak_hook)

# %%
# 现在我们来测试一下 `speak` 的钩子函数

msg = Msg(
    "user",
    "现在，从 1 到 15，步长为 1，用逗号分隔每个数字。",
    "user",
)

msg_response = streaming_agent(msg)

print("Speak 函数的调用次数：", streaming_agent.cnt)


# %%
# Observe 钩子函数
# """""""""""""""""
# 与 speak 钩子函数类似，我们在下面展示如何使用 pre/post-observe 钩子：

import json


def pre_observe_hook(self, x: Union[Msg, list[Msg]]) -> Union[Msg, list[Msg]]:
    """显示消息内容的前置观察钩子。"""
    if isinstance(x, Msg):
        x.content = "观察: " + x.content
    elif isinstance(x, list):
        for msg in x:
            msg.content = "观察: " + msg.content
    return x


def post_observe_hook(self) -> None:
    """显示消息内容的后置观察钩子。"""
    if not hasattr(self, "cnt"):
        setattr(self, "cnt", 0)
    self.cnt += 1


# 首先清除智能体记忆
agent.memory.clear()

agent.register_hook("pre_observe", "pre_observe_hook", pre_observe_hook)
agent.register_hook("post_observe", "post_observe_hook", post_observe_hook)

agent.observe(
    Msg(
        "user",
        "太阳升起来了。",
        "user",
    ),
)

print(
    "智能体的记忆：\n",
    json.dumps(
        [_.to_dict() for _ in agent.memory.get_memory()],
        indent=4,
        ensure_ascii=False,
    ),
)
print("调用 Observe 函数的次数：", agent.cnt)

# %%
# 类级别
# ^^^^^^^^^^^^^^^^^^^^^^
#
# 类级别的钩子函数和对象级别使用逻辑一致，只不过对应的生效范围为类的所有对象，以及注册、删除、清空函数带有 "class" 的前缀。
#
# 下面以 pre_reply 函数为例，展示类级别的钩子函数的使用
#

# 新建一个新的agent对象
agent2 = TestAgent()

AgentBase.clear_all_class_hooks()

print("此时agent的钩子函数：", list(agent._class_hooks_pre_reply.keys()))
print("此时agent2的钩子函数：", list(agent2._class_hooks_pre_reply.keys()))

# %%
# 接着我们在 `AgentBase` 的基类上注册类级别的钩子函数

# 注册类级别的钩子函数
AgentBase.register_class_hook("pre_reply", "class_hook_1", pre_reply_hook_1)

print("注册后agent的钩子函数：", list(agent._class_hooks_pre_reply.keys()))
print("注册后agent2的钩子函数：", list(agent2._class_hooks_pre_reply.keys()))

# %%
# 能够看到，类级别的钩子函数会对该类的所有对象生效。
