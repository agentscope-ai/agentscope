# -*- coding: utf-8 -*-
"""A simple example for auto discussion: the agent builder automatically\
 set up the agent participating the discussion ."""
from tools import load_txt, extract_scenario_and_participants
import agentscope
from agentscope.agent import DialogAgent
from agentscope.pipelines._functional import sequential_pipeline
from agentscope.message import Msg

model_configs = [
    {
        "model_type": "openai_chat",
        "config_name": "gpt-3.5-turbo",
        "model_name": "gpt-3.5-turbo",
        "api_key": "xxx",  # Load from env if not provided
        "organization": "xxx",  # Load from env if not provided
        "generate_args": {
            "temperature": 0.5,
        },
    },
    {
        "model_type": "post_api_chat",
        "config_name": "my_post_api",
        "api_url": "https://xxx",
        "headers": {},
        "json_args": {},
    },
]

agentscope.init(
    model_configs=model_configs,
    project="Self-Organizing Conversation",
)


# init the self-organizing conversation
agent_builder = DialogAgent(
    name="agent_builder",
    sys_prompt="You're a helpful assistant.",
    model_config_name="my_post_api",
)


max_round = 2
query = "Say the pupil of your eye has a diameter of 5 mm and you have a \
telescope with an aperture of 50 cm. How much more light can the \
telescope gather than your eye?"

# get the discussion scenario and participant agent
x = load_txt(
    "examples/conversation_self_organizing/agent_builder_instruct.txt",
).format(
    question=query,
)

x = Msg("user", x, role="user")
settings = agent_builder(x)
scenario_participants = extract_scenario_and_participants(settings.content)

# set the agent that participant the discussion
agents = [
    DialogAgent(
        name=key,
        sys_prompt=val,
        model_config_name="my_post_api",
    )
    for key, val in scenario_participants["Participants"].items()
]

# begin discussion
msg = Msg("user", f"let's discuss to solve the question: {query}", role="user")
for i in range(max_round):
    msg = sequential_pipeline(agents, msg)
