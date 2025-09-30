# -*- coding: utf-8 -*-
"""An example of a conversation with a ReAct agent."""

from agentscope.agent import UserAgent
from agentscope.agent import ReActAgent
from agentscope.service import (
    bing_search,  # or google_search,
    read_text_file,
    write_text_file,
    ServiceToolkit,
    execute_python_code,
)
import agentscope

# Prepare the Bing API key and model configuration
BING_API_KEY = "{YOUR_BING_API_KEY}"

YOUR_MODEL_CONFIGURATION_NAME = "{YOUR_MODEL_CONFIGURATION_NAME}"
YOUR_MODEL_CONFIGURATION = {
    "model_type": "xxx",
    "config_name": YOUR_MODEL_CONFIGURATION_NAME,
    # ...
}

# Prepare the tools for the agent
service_toolkit = ServiceToolkit()

service_toolkit.add(bing_search, api_key=BING_API_KEY, num_results=3)
service_toolkit.add(execute_python_code)
service_toolkit.add(read_text_file)
service_toolkit.add(write_text_file)

agentscope.init(
    model_configs=YOUR_MODEL_CONFIGURATION,
    project="Conversation with ReActAgent",
)

# Create agent
agent = ReActAgent(
    name="assistant",
    model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
    verbose=True,
    service_toolkit=service_toolkit,
)
user = UserAgent(name="User", input_hint="User Input ('exit' to quit): ")

# Build
x = None
while True:
    x = user(x)
    if x.content == "exit":
        break
    x = agent(x)
