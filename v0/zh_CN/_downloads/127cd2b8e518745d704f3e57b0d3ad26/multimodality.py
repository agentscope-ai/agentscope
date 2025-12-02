# -*- coding: utf-8 -*-
"""
.. _multimodality:

多模态
============================

在本节中，我们将展示如何在 AgentScope 中构建多模态应用程序。

构建视觉智能体
------------------------------

对于大多数大语言模型 API，视觉和非视觉模型共享相同的 API，只是输入格式有所不同。
在 AgentScope 中，模型包装器的 `format` 函数负责将输入的 `Msg` 对象转换为视觉模型所需的格式。

也就是说，我们只需指定视觉大语言模型而无需更改智能体的代码。
有关 AgentScope 支持的视觉大语言模型 API，请参阅 :ref:`model_api` 部分。

以 "qwen-vl-max" 为例，我们将使用视觉大语言模型构建一个智能体。
"""

model_config = {
    "config_name": "my-qwen-vl",
    "model_type": "dashscope_multimodal",
    "model_name": "qwen-vl-max",
}

# %%
#
# 如往常一样，我们使用上述配置初始化 AgentScope，并使用视觉大语言模型创建一个新的智能体。
#

from agentscope.agents import DialogAgent
import agentscope

agentscope.init(model_configs=model_config)

agent = DialogAgent(
    name="Monday",
    sys_prompt="你是一个名为Monday的助手。",
    model_config_name="my-qwen-vl",
)

# %%
# 为了与智能体进行多模态数据的交互，`Msg` 类提供了一个 `url` 字段。
# 你可以在 `url` 字段中放置本地或在线的图片 URL。
#
# 这里让我们首先使用 matplotlib 创建一个图片
#

import matplotlib.pyplot as plt

plt.figure(figsize=(6, 6))
plt.bar(range(3), [2, 1, 4])
plt.xticks(range(3), ["Alice", "Bob", "Charlie"])
plt.title("The Apples Each Person Has in 2023")
plt.xlabel("Number of Apples")

plt.show()
plt.savefig("./bar.png")

# %%
# 然后，我们创建一个包含图像 URL 的 `Msg` 对象
#

from agentscope.message import Msg

msg = Msg(
    name="用户",
    content="为我详细描述一下这个图片。",
    role="user",
    url="./bar.png",
)

# %%
# 之后，我们可以将消息发送给视觉智能体并获取响应。

response = agent(msg)
