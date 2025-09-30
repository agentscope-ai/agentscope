# -*- coding: utf-8 -*-
""" Example of using web browser module. """

from webact_agent import WebActAgent
from agentscope.agent import UserAgent

import agentscope

# Fill in your OpenAI API key or create your own vision model configuration
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
    project="Conversation with Web Browser",
)

agent = WebActAgent(
    name="assistant",
    model_config_name="gpt-4o_config",
    verbose=True,
)

user = UserAgent(
    "user",
    input_hint="User Input (type 'exit' to break): ",
)

x = None
while True:
    x = user(x)
    if x.content == "exit":
        break
    x = agent(x)
