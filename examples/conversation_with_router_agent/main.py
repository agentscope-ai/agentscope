# -*- coding: utf-8 -*-
"""The main script for the example of conversation with router agent."""
from router_agent import RouterAgent

import agentscope
from agentscope.agent import DialogAgent, UserAgent

# ================== Prepare model configuration =============================

YOUR_MODEL_CONFIGURATION_NAME = "{YOUR_MODEL_CONFIGURATION_NAME}"
YOUR_MODEL_CONFIGURATION = {
    "config_name": YOUR_MODEL_CONFIGURATION_NAME,
    # ...
}

# ============================================================================

agentscope.init(
    model_configs=YOUR_MODEL_CONFIGURATION,
    project="Conversation with router agent",
)

# Let's build some working agent with different capabilities. For simplicity,
# we just use the same agent. You can replace them with your own agent.
agent_math = DialogAgent(
    name="Math",
    sys_prompt="You are a math assistant to help solve math problems.",
    model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
)

agent_history = DialogAgent(
    name="History",
    sys_prompt="You are an assistant who is good at history.",
    model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
)

# Init a router agent
SYS_PROMPT_ROUTER = """You're a router assistant named {name}.

## YOUR TARGET
1. Given agent with different capabilities, your target is to assign questions to the corresponding agent according to the user requirement.
2. You should make full use of the different abilities of the given agent.
3. If no agent is suitable to answer user's question, then respond directly.

## Agents You Can Use
The agent are listed in the format of "{index}. {agent_name}: {agent_description}"
1. math: An agent who is good at math.
2. history: An agent who is good at history.
"""  # noqa

router_agent = RouterAgent(
    sys_prompt=SYS_PROMPT_ROUTER,
    model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
)

# Init a user agent
user = UserAgent(name="user")

# Start the conversation
msg = None
while True:
    user_msg = user(msg)
    if user_msg.content == "exit":
        break

    # Replied by router agent
    router_msg = router_agent(user_msg)

    # Route the question to the corresponding agent
    if router_msg.metadata == "math":
        msg = agent_math(user_msg)
    elif router_msg.metadata == "history":
        msg = agent_history(user_msg)
    else:
        # Answer the question by router agent directly
        msg = router_msg
