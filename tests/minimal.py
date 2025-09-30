# -*- coding: utf-8 -*-
"""
Minimal case for agentscope
"""
import agentscope
from agentscope.agent import DialogAgent

print(agentscope.__version__)
agentscope.init(
    project="minimal",
    model_configs=[
        {
            "model_type": "dashscope_chat",
            "config_name": "qwen",
            "model_name": "qwen-max",
            "api_key": "xxx",
        },
        {
            "model_type": "openai_chat",
            "config_name": "gpt-4",
            "model_name": "gpt-4",
            "api_key": "xxx",
            "organization": "xxx",
            "generate_args": {"temperature": 0.5},
        },
        {
            "model_type": "post_api_chat",
            "config_name": "my_post_api",
            "model_name": "llama",
            "api_url": "https://xxx",
            "headers": {},
            "json_args": {},
        },
    ],
)
a = DialogAgent(
    name="A",
    sys_prompt="You are a helpful assistant.",
    model_config_name="my_post_api",
)

b = DialogAgent(
    name="B",
    sys_prompt="You are a helpful assistant.",
    model_config_name="qwen",
)

c = DialogAgent(
    name="C",
    sys_prompt="You are a helpful assistant.",
    model_config_name="gpt-4",
)
