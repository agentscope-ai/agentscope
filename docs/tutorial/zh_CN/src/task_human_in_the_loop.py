# -*- coding: utf-8 -*-
"""
.. _human-in-the-loop:

Human-in-the-Loop 工具调用
=========================

在许多生产场景中，我们希望在 **执行工具之前** 让人类先审核或修改调用参数，
例如运行 shell 命令、执行任意 Python 代码、调用外部 MCP 服务等。

AgentScope 通过 ``Toolkit`` 的 ``human_permit_func`` 回调，提供了「人类在环」
（Human-in-the-Loop）的工具调用能力，包括：

- 对本地工具（如 ``execute_shell_command``、``execute_python_code``）加审
- 对 MCP 工具统一加审
- 让用户在运行前 **修改工具名和参数**

.. tip::
   完整可运行示例见仓库中的
   ``examples/functionality/human_in_the_loop/`` 目录。

基本概念
-------------------------

在 ``Toolkit.register_tool_function`` 与 ``Toolkit.register_mcp_client`` 中可以传入
可选参数 ``human_permit_func``，其签名形如：

.. code-block:: python

   from agentscope.message import ToolUseBlock

   def human_permit_function(tool_call: ToolUseBlock) -> bool:
       \"\"\"在工具实际执行前被调用。

       返回值:
           - True: 允许执行工具
           - False: 拒绝执行

       也可以在函数内部原地修改 ``tool_call['name']`` 和 ``tool_call['input']``，
       从而实现「预编辑」工具名与参数。
       注意 ``tool_call['id']`` 字段 **不能** 被修改，它在内部用于标识工具调用。
       \"\"\"
       ...

当智能体计划调用某个工具时，对应的 ``ToolUseBlock`` 会先被传入
``human_permit_function``，只有当它返回 ``True`` 时，工具才会真正执行。

与示例代码保持一致的一个实现如下：

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

       if option == "y":  # 正常执行
           return True
       if option == "n":  # 拒绝执行
           return False

       # 进入编辑模式，允许用户修改工具名和参数
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

       # 原地修改 tool_call
       tool_call["name"] = expected_tool_name
       tool_call["input"].clear()
       tool_call["input"][arg_name_dict[expected_tool_name]] = expected_tool_args
       return True

结合本地工具
-------------------------

对于本地工具（如 ``execute_python_code``、``execute_shell_command``），只需在
注册时传入相同的 ``human_permit_function`` 即可让所有工具统一走「人类在环」
审核流程：

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

结合 MCP 工具
-------------------------

``Toolkit`` 同样支持为 MCP 工具指定 ``human_permit_func``。例如，在
``examples/functionality/human_in_the_loop/main.py`` 中，通过
``HttpStatefulClient`` 连接本地 MCP 服务器，并将其工具注册到 ``Toolkit``：

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

此时，无论是本地工具还是 MCP 工具，它们的每一次调用都会先经过
``human_permit_function`` 审核，从而实现统一的「人类在环」策略。
"""
