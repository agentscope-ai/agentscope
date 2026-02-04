# -*- coding: utf-8 -*-
"""
.. _tool:

Tool
=========================

To ensure accurate and reliable tool parsing, AgentScope fully embraces the use of tools API with the following features:

- Support **automatic** tool parsing from Python functions with their docstrings
- Support both **synchronous and asynchronous** tool functions
- Support **streaming** tool responses (either synchronous or asynchronous generators)
- Support **dynamic extension** to the tool JSON Schema
- Support **interrupting** the tool execution with proper signal handling
- Support **autonomous tool management** by agents

All above features are implemented by the ``Toolkit`` class in AgentScope, which is responsible for managing tool functions and their execution.

.. tip:: The support of MCP (Model Context Protocol) refers to the :ref:`mcp` section.
"""
import asyncio
import inspect
import json
from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field

import agentscope
from agentscope.message import TextBlock, ToolUseBlock
from agentscope.tool import ToolResponse, Toolkit, execute_python_code


# %%
# Tool Function
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# In AgentScope, a tool function is a Python function that
#
# - returns a ``ToolResponse`` object or a generator that yields ``ToolResponse`` objects
# - has a docstring that describes the tool's functionality and parameters
#
# A template of a tool function is as follows:


def tool_function(a: int, b: str) -> ToolResponse:
    """{function description}

    Args:
        a (int):
            {description of the first parameter}
        b (str):
            {description of the second parameter}
    """


# %%
# .. tip:: Instance method and class method can also be used as tool functions, and the ``self`` and ``cls`` parameters will be ignored.
#
# AgentScope provides several built-in tool functions under the ``agentscope.tool`` module, such as ``execute_python_code``, ``execute_shell_command`` and text file write/read functions.
#

print("Built-in Tool Functions:")
for _ in agentscope.tool.__all__:
    if _ not in ["Toolkit", "ToolResponse"]:
        print(_)

# %%
# Toolkit
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# The ``Toolkit`` class is designed to manage tool functions, extracting their JSON Schema from docstrings and providing a unified interface for tool execution.
#
# Basic Usage
# ------------------------------
# The basic functionality of the ``Toolkit`` class is to register tool functions and execute them.
#


# Prepare a custom tool function
async def my_search(query: str, api_key: str) -> ToolResponse:
    """A simple example tool function.

    Args:
        query (str):
            The search query.
        api_key (str):
            The API key for authentication.
    """
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"Searching for '{query}' with API key '{api_key}'",
            ),
        ],
    )


# Register the tool function in a toolkit
toolkit = Toolkit()
toolkit.register_tool_function(my_search)

# %%
# When registering a tool function, you can get its JSON Schema by calling the ``get_json_schemas`` method.
#

print("Tool JSON Schemas:")
print(json.dumps(toolkit.get_json_schemas(), indent=4, ensure_ascii=False))

# %%
# ``Toolkit`` also allows developers to preset the arguments for tool functions, especially useful for API keys or other sensitive information.
#

# Clear the toolkit first
toolkit.clear()

# Register tool function with preset keyword arguments
toolkit.register_tool_function(my_search, preset_kwargs={"api_key": "xxx"})

print("Tool JSON Schemas with Preset Arguments:")
print(json.dumps(toolkit.get_json_schemas(), indent=4, ensure_ascii=False))

# %%
# In ``Toolkit``, the ``call_tool_function`` method takes a tool use block as input and executes the corresponding tool function, returning **a unified asynchronous generator** that yields ``ToolResponse`` objects.
#


async def example_tool_execution() -> None:
    """Example of executing a tool call."""
    res = await toolkit.call_tool_function(
        ToolUseBlock(
            type="tool_use",
            id="123",
            name="my_search",
            input={"query": "AgentScope"},
        ),
    )

    # Only one tool response is expected in this case
    print("Tool Response:")
    async for tool_response in res:
        print(tool_response)


asyncio.run(example_tool_execution())

# %%
# Extending JSON Schema Dynamically
# --------------------------------------
#
# Toolkit allows to extend the JSON schemas of tool functions dynamically by calling the ``set_extended_model`` method.
# Such feature allows to add more parameters to the tool function without modifying its original definition.
#
# .. tip:: Related scenarios include dynamic :ref:`structured-output` and CoT (Chain of Thought) reasoning
#
# .. note:: The function to be extended should accept variable keyword arguments (``**kwargs``), so that the additional fields can be passed to it.
#
# Taking the CoT reasoning as an example, we can extend all tool functions with a ``thinking`` field, allowing the agent to summarize the current state and then decide what to do next.
#


# Example tool function
def tool_function(**kwargs: Any) -> ToolResponse:
    """A tool function"""
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"Received parameters: {kwargs}",
            ),
        ],
    )


# Add a thinking field so that the agent could think before giving the other parameters.
class ThinkingModel(BaseModel):
    """A Pydantic model for additional fields."""

    thinking: str = Field(
        description="Summarize the current state and decide what to do next.",
    )


# Register
toolkit.set_extended_model("my_search", ThinkingModel)

print("The extended JSON Schema:")
print(json.dumps(toolkit.get_json_schemas(), indent=4, ensure_ascii=False))

# %%
# Interrupting Tool Execution
# ------------------------------
# The ``Toolkit`` class supports **execution interruption** of **async tool functions** and provides a comprehensive **agent-oriented post-processing mechanism**.
# Such interruption is implemented based on the asyncio cancellation mechanism, and the post-processing varies depending on the return type of tool function.
#
# .. note:: For synchronous tool functions, their execution cannot be interrupted by asyncio cancellation. So the interruption is handled within the agent rather than the toolkit.
#  Refer to the :ref:`agent` section for more information.
#
# Specifically, if the tool function returns a ``ToolResponse`` object, a predefined ``ToolResponse`` object with an interrupted message will be yielded.
# So that the agent can observe the interruption and handle it accordingly.
# Besides, a flag ``is_interrupted`` will be set to ``True`` in the response, and the external caller can decide whether to throw the ``CancelledError`` exception to the outer layer.
#
# An example of async tool function that can be interrupted is as follows:
#


async def non_streaming_function() -> ToolResponse:
    """A non-streaming tool function that can be interrupted."""
    await asyncio.sleep(1)  # Simulate a long-running task

    # Fake interruption for demonstration
    raise asyncio.CancelledError()

    # The following code won't be executed due to the cancellation
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="Run successfully!",
            ),
        ],
    )


async def example_tool_interruption() -> None:
    """Example of tool interruption."""
    toolkit = Toolkit()
    toolkit.register_tool_function(non_streaming_function)
    res = await toolkit.call_tool_function(
        ToolUseBlock(
            type="tool_use",
            id="123",
            name="non_streaming_function",
            input={},
        ),
    )

    async for tool_response in res:
        print("Tool Response:")
        print(tool_response)
        print("The interrupted flag:")
        print(tool_response.is_interrupted)


asyncio.run(example_tool_interruption())

# %%
# For streaming tool functions, which returns an asynchronous generator, the ``Toolkit`` will attach the interrupted message to the previous chunk of the response.
# By this way, the agent can observe what the tool has returned before the interruption.
#
# The example of interrupting a streaming tool function is as follows:
#


async def streaming_function() -> AsyncGenerator[ToolResponse, None]:
    """A streaming tool function that can be interrupted."""
    # Simulate a chunk of response
    yield ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="1234",
            ),
        ],
        stream=True,
    )

    # Simulate interruption
    raise asyncio.CancelledError()

    # The following code won't be executed due to the cancellation
    yield ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="123456789",
            ),
        ],
    )


async def example_streaming_tool_interruption() -> None:
    """Example of streaming tool interruption."""
    toolkit = Toolkit()
    toolkit.register_tool_function(streaming_function)

    res = await toolkit.call_tool_function(
        ToolUseBlock(
            type="tool_use",
            id="xxx",
            name="streaming_function",
            input={},
        ),
    )

    i = 0
    async for tool_response in res:
        print(f"Chunk {i}:")
        print(tool_response)
        print("The interrupted flag: ", tool_response.is_interrupted, "\n")
        i += 1


asyncio.run(example_streaming_tool_interruption())

# %%
# Automatic Tool Management
# -------------------------------------
# .. image:: https://img.alicdn.com/imgextra/i3/O1CN013cvRpO27MfesMsTeh_!!6000000007783-2-tps-840-521.png
#     :width: 100%
#     :align: center
#     :alt: Automatic Tool Management
#
#
# The ``Toolkit`` class supports **automatic tool management** by introducing the concept of **tool group**, as well as a **meta tool function** named ``reset_equipped_tools``.
#
# The tool group is a set of related tool functions, e.g. browser-use tools, map services tools, etc., which will be managed together.
# Only the tools in the activated groups will be visible to agents, i.e. accessible by the ``toolkit.get_json_schemas()`` method.
#
# Note there is a special group called ``basic``, which is always activated and the tools registered without specifying the group name will be added to this group by default.
#
# .. tip:: The ``basic`` group ensures that the basic usage of tools won't be affected by the group features if you don't need them.
#
# Now we try to create a tool group named ``browser_use``, which contains some web browsing tools.
#


def navigate(url: str) -> ToolResponse:
    """Navigate to a web page.

    Args:
        url (str):
            The URL of the web page to navigate to.
    """
    pass


def click_element(element_id: str) -> ToolResponse:
    """Click an element on the web page.

    Args:
        element_id (str):
            The ID of the element to click.
    """
    pass


toolkit = Toolkit()

# Create a tool group named browser_use
toolkit.create_tool_group(
    group_name="browser_use",
    description="The tool functions for web browsing.",
    active=False,
    # The notes when using these tools
    notes="""1. Use ``navigate`` to open a web page.
2. When requiring user authentication, ask the user for the credentials
3. ...""",
)

toolkit.register_tool_function(navigate, group_name="browser_use")
toolkit.register_tool_function(click_element, group_name="browser_use")

# We can also register some basic tools
toolkit.register_tool_function(execute_python_code)

# %%
# If we check the tools JSON schema, we can only see the ``execute_python_code`` tool, because the ``browser_use`` group is not activated yet:

print("Tool JSON Schemas with Group:")
print(json.dumps(toolkit.get_json_schemas(), indent=4, ensure_ascii=False))

# %%
# Use the ``update_tool_groups`` method to activate or deactivate tool groups:

toolkit.update_tool_groups(group_names=["browser_use"], active=True)

print("Tool JSON Schemas with Group:")
print(json.dumps(toolkit.get_json_schemas(), indent=4, ensure_ascii=False))

# %%
# Additionally, ``Toolkit`` provides a meta tool function named ``reset_equipped_tools``, taking the current group names as the argument to indicate which groups to activate:
#
# .. note:: In ``ReActAgent`` class, you can enable the meta tool function by setting ``enable_meta_tool=True`` in the constructor.
#

# Register the meta tool function
toolkit.register_tool_function(toolkit.reset_equipped_tools)

reset_equipped = next(
    tool
    for tool in toolkit.get_json_schemas()
    if tool["function"]["name"] == "reset_equipped_tools"
)
print("JSON schema of the ``reset_equipped_tools`` function:")
print(
    json.dumps(
        reset_equipped,
        indent=4,
        ensure_ascii=False,
    ),
)

# %%
# When agent calls the ``reset_equipped_tools`` function, the corresponding tool groups will be activated, and the tool response will
# contain the notes of the activated tool groups.
#


async def mock_agent_reset_tools() -> None:
    """Mock agent to reset tool groups."""
    # Call the meta tool function
    res = await toolkit.call_tool_function(
        ToolUseBlock(
            type="tool_use",
            id="154",
            name="reset_equipped_tools",
            input={
                "browser_user": True,
            },
        ),
    )

    async for tool_response in res:
        print("Text content in tool Response:")
        print(tool_response)


asyncio.run(mock_agent_reset_tools())

# %%
# The toolkit also provides a method to gather the notes of the activated tool groups, and you can assemble it into your agent's system prompt.
#
# .. tip:: The automatic tool management feature is already implemented in the ``ReActAgent`` class, refer to the :ref:`agent` section for more details.
#

# Create one more tool group
toolkit.create_tool_group(
    group_name="map_service",
    description="The google map service tools.",
    active=True,
    notes="""1. Use ``get_location`` to get the location of a place.
2. ...""",
)

print("The gathered notes of the activated tool groups:")
print(toolkit.get_activated_notes())

# %%
# Further Reading
# ---------------------
# - :ref:`agent`
# - :ref:`state`
# - :ref:`mcp`
#
