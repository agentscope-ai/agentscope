# -*- coding: utf-8 -*-
"""
.. _multimodality:

MultiModality
============================

In this section, we will show how to build multimodal applications in AgentScope with two examples.

- The first example demonstrates how to use vision LLMs within an agent, and
- the second example shows how to use text to image generation within an agent.

Building Vision Agent
------------------------------

For most LLM APIs, the vision and non-vision LLMs share the same APIs, and only differ in the input format.
In AgentScope, the `format` function of the model wrapper is responsible for converting the input `Msg` objects into the required format for vision LLMs.

That is, we only need to specify the vision LLM without changing the agent's code.
Taking "qwen-vl-max" as an example, its model configuration is the same as the non-vision LLMs in DashScope Chat API.

Refer to section :ref:`model_api` for the vision LLM APIs supported in AgentScope.
"""

model_config = {
    "config_name": "my-qwen-vl",
    "model_type": "dashscope_multimodal",
    "model_name": "qwen-vl-max",
}

# %%
#
# As usual, we initialize AgentScope with the above configuration, and create a new agent with the vision LLM.

from agentscope.agents import DialogAgent
import agentscope

agentscope.init(model_configs=model_config)

agent = DialogAgent(
    name="Monday",
    sys_prompt="You're a helpful assistant named Monday.",
    model_config_name="my-qwen-vl",
)

# %%
# To communicate with the vision agent with pictures, `Msg` class provides an `url` field.
# You can put both local or online image URL(s) in the `url` field.
#
# Let's first create an image with matplotlib

import matplotlib.pyplot as plt

plt.figure(figsize=(6, 6))
plt.bar(range(3), [2, 1, 4])
plt.xticks(range(3), ["Alice", "Bob", "Charlie"])
plt.title("The Apples Each Person Has in 2023")
plt.xlabel("Number of Apples")

plt.show()
plt.savefig("./bar.png")

# %%
# Then, we create a `Msg` object with the image URL

from agentscope.message import Msg

msg = Msg(
    name="User",
    content="Describe the attached image for me.",
    role="user",
    url="./bar.png",
)

# %%
# After that, we can send the message to the vision agent and get the response.

response = agent(msg)
