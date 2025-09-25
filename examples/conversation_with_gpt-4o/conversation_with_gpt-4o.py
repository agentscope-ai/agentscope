# -*- coding: utf-8 -*-
"""An example for conversation with OpenAI vision model, especially for
GPT-4o."""
import agentscope
from agentscope.agents import UserAgent, DialogAgent

# Fill in your OpenAI API key
YOUR_OPENAI_API_KEY = "xxx"

model_config = {
    "config_name": "gpt-4o_config",
    "model_type": "openai_chat",
    "model_name": "gpt-4o",
    "api_key": YOUR_OPENAI_API_KEY,
    "generate_args": {
        "temperature": 0.7,
    },
}

agentscope.init(
    model_configs=model_config,
    project="Conversation with GPT-4o",
)

# Require user to input URL, and press enter to skip the URL input
user = UserAgent("user", require_url=True)

agent = DialogAgent(
    "Friday",
    sys_prompt="You're a helpful assistant named Friday.",
    model_config_name="gpt-4o_config",
)

x = None
while True:
    x = agent(x)
    x = user(x)
    if x.content == "exit":  # type "exit" to break the loop
        break
