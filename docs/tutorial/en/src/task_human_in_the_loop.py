# -*- coding: utf-8 -*-
"""
.. _human-in-the-loop:

Human-in-the-Loop Tool Calling
==============================

In many production scenarios, we want a human to **review or edit tool calls
before execution**, e.g. running shell commands, executing arbitrary Python
code, or invoking external MCP services.

AgentScope provides a Human-in-the-Loop mechanism for tools via the
``human_permit_func`` callback in ``Toolkit``, which allows you to:

- add human review for local tools (e.g. ``execute_shell_command``,
  ``execute_python_code``),
- add human review for MCP tools,
- let the user **edit tool name and parameters** before execution.

.. tip::
   The full runnable example is available in
   ``examples/functionality/human_in_the_loop/``.

Basic concept
-------------------------

Both ``Toolkit.register_tool_function`` and ``Toolkit.register_mcp_client``
accept an optional ``human_permit_func`` parameter with the following signature:

.. code-block:: python

   from agentscope.message import ToolUseBlock

   def human_permit_function(tool_call: ToolUseBlock) -> bool:
       \"\"\"This function is called right before a tool is executed.

       Returns:
           - True: permit the tool call
           - False: reject the tool call

       It can also modify ``tool_call['name']`` and ``tool_call['input']`` in
       place to implement pre-editing of the tool name and arguments.
       Note that ``tool_call['id']`` **must not** be modified, as it is used
       internally to track the tool call.
       \"\"\"
       ...

When an agent decides to call a tool, the corresponding ``ToolUseBlock`` is
first passed into ``human_permit_function``. The tool will be executed only if
the function returns ``True``.

The following implementation is consistent with the example:

.. code-block:: python

   from agentscope.message import ToolUseBlock

   def human_permit_function(tool_call: ToolUseBlock) -> bool:
       arg_name_dict = {
           "execute_python_code": "code",
           "execute_shell_command": "command",
           "add_one": "a",
       }
       option = None
       while option not in ["y", "n", "e"]:
           option = (
               input(
                   "Enter 'y' for agreement, 'n' for refusal, "
                   "'e' to modify execution parameters: ",
               )
               .strip()
               .lower()
           )

       if option == "y":  # execute as-is
           return True
       if option == "n":  # refuse
           return False

       # edit mode: allow user to change tool name and arguments
       expected_tool_name = ""
       while expected_tool_name not in [
           "execute_python_code",
           "execute_shell_command",
           "add_one",
       ]:
           expected_tool_name = input(
               "Enter the expected tool name registered in the toolkit, "
               "available options: "
               "execute_python_code, execute_shell_command, add_one: ",
           ).strip()

       expected_tool_args = input(
           f"Enter {arg_name_dict[expected_tool_name]} "
           f"for {expected_tool_name}: ",
       )  # your code or command

       # modify the tool_call in place
       tool_call["name"] = expected_tool_name
       tool_call["input"].clear()
       tool_call["input"][arg_name_dict[expected_tool_name]] = expected_tool_args
       return True

With local tools
-------------------------

For local tools such as ``execute_python_code`` and ``execute_shell_command``,
you can pass the same ``human_permit_function`` when registering them so that
all tools share a unified Human-in-the-Loop review process:

.. code-block:: python

   from agentscope.tool import (
       Toolkit,
       execute_shell_command,
       execute_python_code,
   )

   toolkit = Toolkit()
   toolkit.register_tool_function(
       execute_shell_command,
       human_permit_func=human_permit_function,
   )
   toolkit.register_tool_function(
       execute_python_code,
       human_permit_func=human_permit_function,
   )

With MCP tools
-------------------------

``Toolkit`` also supports specifying ``human_permit_func`` for MCP tools.
In ``examples/functionality/human_in_the_loop/main.py``, a local MCP server is
connected via ``HttpStatefulClient`` and its tools are registered into
``Toolkit``:

.. code-block:: python

   from agentscope.mcp import HttpStatefulClient

   add_mcp_client = HttpStatefulClient(
       name="mcp_add_one",
       transport="sse",
       url="http://127.0.0.1:8001/sse",
   )

   await add_mcp_client.connect()
   await toolkit.register_mcp_client(
       add_mcp_client,
       human_permit_func=human_permit_function,
   )

In this way, **both local tools and MCP tools** will go through the same
``human_permit_function`` before execution, implementing a unified
Human-in-the-Loop policy.
"""
