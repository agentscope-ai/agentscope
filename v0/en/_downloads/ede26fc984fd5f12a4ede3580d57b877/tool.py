# -*- coding: utf-8 -*-
"""
.. _tools:

Tools
====================

Background
--------------------

There are two ways to call tools in LLM-empowered multi-agent applications.

.. image:: https://img.alicdn.com/imgextra/i3/O1CN01iizvjY1UjKCE3q5FR_!!6000000002553-0-tps-1830-792.jpg
   :align: center
   :alt: Two ways for tool calling
   :width: 80%

- Prompt-based tool calling: Developers introduce tools in the prompt and extract tool calls from the LLM response.
- API-based tool calling: Developers provide tools description in JSON schema format. The LLM API will directly return the tool calls in their specific format.

AgentScope supports both ways. In this tutorial, we will introduce how to use
the built-in tools and how to create custom tools.
"""
import json

import agentscope
from agentscope.message import Msg
from agentscope.models import DashScopeChatWrapper

# %%
# Using Built-in Tools
# --------------------------
# AgentScope provides a `ServiceToolkit` module that supports to
#
# - parse tools into JSON schemas automatically
# - check arguments and call functions
#
# Before using `ServiceToolkit`, we can take a look at the available tools in
# the `agentscope.service` module.

from agentscope.service import get_help, ServiceResponse, ServiceExecStatus

get_help()

# %%
# All above functions are implemented as Python functions.
# They can be registered to the `ServiceToolkit` by calling the `add` method.
# The `ServiceToolkit` will parse the tool functions into JSON schema
# automatically.

from agentscope.service import ServiceToolkit
from agentscope.service import bing_search, execute_shell_command

toolkit = ServiceToolkit()
toolkit.add(execute_shell_command)

# %%
# Note some parameters of the tool functions (e.g. api_key) should be handled
# by developers.
# You can directly pass these parameters as keyword arguments in the add
# method as follows, the reserved parameters will be left to the agent to fill.

toolkit.add(bing_search, api_key="xxx")

print("The tools instruction:")
print(toolkit.tools_instruction)

# %%
# The built-in default calling format:

print(toolkit.tools_calling_format)

# %%
# The JSON Schema description of the tool functions:

print(json.dumps(toolkit.json_schemas, indent=2))

# %%
# Prompt-based Tool Calling
# --------------------------
# In prompt-based tool calling, developers need to
# - introduce the tools and call format in prompt
# - parse and extract the tool calls from the LLM response.
#
# You can use the parsers in :ref:`structured-output` section to parse the LLM
# response and extract the tool calls.
# The tool call format of `ServiceToolkit` is as follows:

from agentscope.message import ToolUseBlock

tool_call = ToolUseBlock(
    type="tool_use",
    id="xxx",
    name="bing_search",
    input={"query": "AgentScope"},
)

# %%
# After assembling the `ServiceToolkit`, you can integrate it into agent.
#
# In AgentScope, we provide a `ReActAgent` to handle the tool usage, you can
# directly pass the `ServiceToolkit` object into this agent.
# Refer to :ref:`builtin-agent` for implementation details of this agent.
#
# .. note:: `ReActAgent` constructs the prompt and parses the tools locally,
#  rather than through the tools API provided by the model API. For using the
#  tools API, please refer to :ref:`tools-api`.

from agentscope.agents import ReActAgent

agentscope.init(
    model_configs={
        "config_name": "my-qwen-max",
        "model_type": "dashscope_chat",
        "model_name": "qwen-max",
    },
)

agent = ReActAgent(
    name="Friday",
    model_config_name="my-qwen-max",
    service_toolkit=toolkit,
    sys_prompt="You're a helpful assistant named Friday.",
)

msg_task = Msg(
    "user",
    "Help me to calculate 1615114134*4343434343",
    "user",
)

res = agent(msg_task)

# %%
# API-based Tool Calling
# --------------------------
#
# In API-based tool calling, developers only need to prepare the tools
# description in JSON schema format. However, different APIs differ in
# - the format of the tool description, and
# - how to construct the prompt with tool calls and execution results.
#
# .. image:: https://img.alicdn.com/imgextra/i4/O1CN01HIHrnw21LrDxzrB4a_!!6000000006969-0-tps-1920-1080.jpg
#    :align: center
#    :alt: API-based tool calling
#    :width: 100%
#
# The above figure takes OpenAI as an example to show how API-based tool
# calling works in AgentScope. We block API-specific requirements by
# `agentscope.formatter` and `ModelResponse` modules. All developers need to
# know is
#
# 1. `ServiceToolkit` will parse the tool functions into standard JSON schema automatically
# 2. `Formatter` class will transform the JSON schemas and messages into the required format
# 3. The tool calls are all unified into the same format (`ToolUseBlock`) within `ModelResponse`
#
# .. tip:: A new agent class `ReActAgentV2` is added for API-based tools calling!
#
# .. note:: Currently, only the `format_chat` method supports tools API. The
#  `format_multi_agent` method will be supported in the future.
#
# .. note:: API-based tool calling does not support streaming return yet, and
#  the related functionality is under development.
#
# Here we take DashScope as an example to show how to use the tools API.

from agentscope.formatters import DashScopeFormatter
from agentscope.message import TextBlock, ToolUseBlock, ToolResultBlock

model = DashScopeChatWrapper(
    config_name="_",
    model_name="qwen-max",
)

# %%
# Step 3 -> 4 in the figure, formating messages and JSON schemas:
msgs = [
    Msg("user", "Help me to execute shell cmd 'whoami'", "user"),
]

formatted_msgs = DashScopeFormatter.format_chat(msgs)
formatted_schemas = DashScopeFormatter.format_tools_json_schemas(
    toolkit.json_schemas,
)
print(json.dumps(formatted_msgs, indent=4, ensure_ascii=False))

# %%
# Step 5 -> 6 -> 7 in the figure, getting the model response:

response = model(formatted_msgs, tools=formatted_schemas)
print("tool_calls:", json.dumps(response.tool_calls, indent=4))

# %%
# Step 8, creating a new message with the tool calls:

# Create a new msg with the tool calls
content = []
if response.text:
    content.append(TextBlock(type="text", text=response.text))
if response.tool_calls:
    content.extend(response.tool_calls)
msgs.append(Msg("assistant", content, "assistant", echo=True))

# execute the tool calls
msg_execution = toolkit.parse_and_call_func(
    response.tool_calls,
    tools_api_mode=True,  # Must be ture for tools API
)

# %%
# Step 9, adding the execution results to the message list:

msgs.append(msg_execution)

# %%
# Now, let's try to format the new message list with tool calls and result
# again!

formatted_msgs = DashScopeFormatter.format_chat(msgs)
print(json.dumps(formatted_msgs, indent=4, ensure_ascii=False))


# %%
# Up to now, we have already finished the API-based tool calling process.
# The whole process refers to the implementation of
# `agentscope.agents.ReActAgentV2` class. You can also directly use this
# agent.

# %%
# Using MCP with ServiceToolkit
# -------------------------------
# AgentScope provides support for integrating MCP (Model Context Protocol)
# servers, enabling enhanced capabilities for models and tools. You can add
# MCP servers to the `ServiceToolkit` using the `add_mcp_servers` method,
# where you specify the configurations for each server.
# Please note that MCP requires Python version >= 3.10.

configs = {
    "mcpServers": {
        "puppeteer": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        },
    },
}

# %%
# Add MCP server configurations to the ServiceToolkit
# `toolkit.add_mcp_servers(server_configs=configs)`
#
# Creating Custom Tools
# --------------------------
# A custom tool function must follow these rules:
#
# - Typing for arguments
# - Well-written docstring in Google style
# - The return of the function must be wrapped by `ServiceResponse`
#
# After calling the `toolkit.add` function, the tool function will be parsed
# automatically and registered to the `ServiceToolkit`.


def new_function(arg1: str, arg2: int) -> ServiceResponse:
    """A brief introduction of this function in one line.

    Args:
        arg1 (`str`):
            Brief description of arg1
        arg2 (`int`):
            Brief description of arg2
    """
    return ServiceResponse(
        status=ServiceExecStatus.SUCCESS,
        content="Done!",
    )
