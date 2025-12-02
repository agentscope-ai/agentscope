# -*- coding: utf-8 -*-
"""
.. _configuring_and_monitoring:

Configuring and Monitoring
==================================

The main entry of AgentScope is `agentscope.init`, where you can configure your application.
"""

import agentscope


agentscope.init(
    model_configs=[
        {  # The model configurations
            "config_name": "my-qwen-max",
            "model_type": "dashscope_chat",
            "model_name": "qwen-max",
        },
    ],
    project="Project Alpha",  # The project name
    name="Test-1",  # The runtime name
    disable_saving=False,  # The main switch to disable saving
    save_dir="./runs",  # The saving directory
    save_log=True,  # Save the logging or not
    save_code=False,  # Save the code for this runtime
    save_api_invoke=False,  # Save the API invocation
    cache_dir="~/.cache",  # The cache directory, used for caching embeddings and so on
    use_monitor=True,  # Monitor the token usage or not
    logger_level="INFO",  # The logger level
)

# %%
# Exporting the configuration
# --------------------------------
# The `state_dict` method can be used to export the configuration of the running application.

import json

print(json.dumps(agentscope.state_dict(), indent=2, ensure_ascii=False))

# %%
# Monitoring the Runtime
# --------------------------
# AgentScope provides AgentScope Studio, a web visual interface to monitor and manage the running applications and histories.
# Refer to section :ref:`visual` for more details.

# %%
# .. _token_usage:
#
# Monitoring Token Usage
# ------------------------
# `print_llm_usage` will print and return the token usage of the current running application.

from agentscope.models import DashScopeChatWrapper

qwen_max = DashScopeChatWrapper(
    config_name="-",
    model_name="qwen-max",
)
qwen_plus = DashScopeChatWrapper(
    config_name="-",
    model_name="qwen-plus",
)

# Call qwen-max and qwen-plus to simulate the token usage
_ = qwen_max([{"role": "user", "content": "Hi!"}])
_ = qwen_plus([{"role": "user", "content": "Who are you?"}])

usage = agentscope.print_llm_usage()

print(json.dumps(usage, indent=2, ensure_ascii=False))
