# -*- coding: utf-8 -*-
"""
.. _tool:

工具
=========================

为了确保准确可靠的工具解析，AgentScope 全面支持工具 API 的使用，具有以下特性：

- 支持从文档字符串 **自动** 解析工具函数
- 支持 **同步和异步** 工具函数
- 支持 **流式** 工具响应（同步或异步生成器）
- 支持对工具 JSON Schema 的 **动态扩展**
- 支持用户实时 **中断** 工具的执行
- 支持智能体的 **自主工具管理**

所有上述功能都由 AgentScope 中的 ``Toolkit`` 类实现，该类负责管理工具函数及其执行。

.. tip:: MCP（模型上下文协议）的支持请参考 :ref:`mcp` 部分。
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
# 工具函数
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# 在 AgentScope 中，工具函数是一个 Python 的可调用对象，它
#
# - 返回一个 ``ToolResponse`` 对象或产生 ``ToolResponse`` 对象的生成器（可以是异步或同步）
# - 具有描述工具功能和参数的文档字符串
#
# 工具函数的模板如下：


def tool_function(a: int, b: str) -> ToolResponse:
    """{函数描述}

    Args:
        a (int):
            {第一个参数的描述}
        b (str):
            {第二个参数的描述}
    """


# %%
# .. tip:: 实例方法和类方法也可以用作工具函数，``Toolkit`` 中将自动忽略 ``self`` 和 ``cls`` 参数。
#
# AgentScope 在 ``agentscope.tool`` 模块下提供了几个内置工具函数，如 ``execute_python_code``、``execute_shell_command`` 和文本文件读写函数。
#

print("内置工具函数：")
for _ in agentscope.tool.__all__:
    if _ not in ["Toolkit", "ToolResponse"]:
        print(_)

# %%
# 工具模块（Toolkit）
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# ``Toolkit`` 类设计用于管理工具函数，从文档字符串中提取它们的 JSON Schema，并为工具执行提供统一接口。
#
# 基本用法
# ------------------------------
# ``Toolkit`` 类的基本功能是注册工具函数并执行它们。
#


# 准备一个自定义工具函数
async def my_search(query: str, api_key: str) -> ToolResponse:
    """一个简单的示例工具函数。

    Args:
        query (str):
            搜索查询。
        api_key (str):
            用于身份验证的 API 密钥。
    """
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"正在使用 API 密钥 '{api_key}' 搜索 '{query}'",
            ),
        ],
    )


# 在工具模块中注册工具函数
toolkit = Toolkit()
toolkit.register_tool_function(my_search)

# %%
# 注册工具函数后，可以通过调用 ``get_json_schemas`` 方法获取其 JSON Schema。
#

print("工具 JSON Schemas：")
print(json.dumps(toolkit.get_json_schemas(), indent=4, ensure_ascii=False))

# %%
# ``Toolkit`` 还允许开发者为工具函数预设参数，这对于 API 密钥或其他敏感信息特别有用。
#

# 先清空工具模块
toolkit.clear()

# 使用预设关键字参数注册工具函数
toolkit.register_tool_function(my_search, preset_kwargs={"api_key": "xxx"})

print("带预设参数的工具 JSON Schemas：")
print(json.dumps(toolkit.get_json_schemas(), indent=4, ensure_ascii=False))

# %%
# 预设参数后，该参数将从 JSON schema 中被移除，并在工具调用时自动传递给该工具函数。
#
# 在 ``Toolkit`` 中，``call_tool_function`` 方法以 ``ToolUseBlock`` 作为输入执行指定的工具函数，统一返回一个 **异步生成器**，该生成器产生 ``ToolResponse`` 对象。
#
# .. note:: AgentScope 中，流式返回的工具函数应该是 **“累积的”**，即当前块的内容应包含之前所有块的内容。
#


async def example_tool_execution() -> None:
    """工具调用执行示例。"""
    res = await toolkit.call_tool_function(
        ToolUseBlock(
            type="tool_use",
            id="123",
            name="my_search",
            input={"query": "AgentScope"},
        ),
    )

    # 非流式返回的工具函数只有一个 ToolResponse 返回
    print("工具响应：")
    async for tool_response in res:
        print(tool_response)


asyncio.run(example_tool_execution())

# %%
# 动态扩展 JSON Schema
# --------------------------------------
#
# Toolkit 允许通过调用 ``set_extended_model`` 方法动态扩展工具函数的 JSON schemas。
# 这种功能允许开发者在不修改工具函数原始定义的情况下，向工具函数添加更多参数。
#
# .. tip:: 相关场景包括动态 :ref:`structured-output` 和 CoT（思维链）推理
#
# .. note:: 要扩展的工具函数应该接受可变关键字参数（``**kwargs``），以便附加字段可以传递给它。
#
# 以 CoT 推理为例，我们可以用 ``thinking`` 字段扩展所有工具函数，允许智能体总结当前状态然后决定下一步做什么。
#


# 示例工具函数
def tool_function(**kwargs: Any) -> ToolResponse:
    """一个工具函数"""
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"接收到的参数：{kwargs}",
            ),
        ],
    )


# 添加一个思考字段，以便智能体在给出其他参数之前可以思考。
class ThinkingModel(BaseModel):
    """用于附加字段的 Pydantic 模型。"""

    thinking: str = Field(
        description="总结当前状态并决定下一步做什么。",
    )


# 注册
toolkit.set_extended_model("my_search", ThinkingModel)

print("扩展后的 JSON Schema：")
print(json.dumps(toolkit.get_json_schemas(), indent=4, ensure_ascii=False))

# %%
# 中断工具执行
# ------------------------------
# ``Toolkit`` 类支持 **异步工具函数** 的 **执行中断**，并提供 **面向智能体的后处理机制**。
# 这种中断基于 asyncio 取消机制实现，其后处理过程根据工具函数的返回类型而有所不同。
#
# .. note:: 对于同步（工具）函数，它们的执行无法通过 asyncio 取消来中断。因此其中断在智能体内而不是工具模块内处理。
#  有关更多信息，请参考 :ref:`agent` 部分。
#
# 具体来说，如果工具函数返回 ``ToolResponse`` 对象，将产生一个带有中断消息的 ``ToolResponse`` 对象。
# 这样智能体可以观察到这一中断并相应地处理它。
# 此外，该 ``ToolResponse`` 对象中的 ``is_interrupted`` 将设置为 ``True``，外部调用者可以决定是否将 ``CancelledError`` 异常抛出到外层。
#
# 可以被中断的异步工具函数示例如下：
#


async def non_streaming_function() -> ToolResponse:
    """一个可以被中断的非流式工具函数。"""
    await asyncio.sleep(1)  # 模拟长时间运行的任务

    # 为演示目的模拟中断
    raise asyncio.CancelledError()

    # 由于取消，以下代码不会被执行
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="运行成功！",
            ),
        ],
    )


async def example_tool_interruption() -> None:
    """工具中断示例。"""
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
        print("工具响应：")
        print(tool_response)
        print("中断标志：")
        print(tool_response.is_interrupted)


asyncio.run(example_tool_interruption())

# %%
# 对于流式工具函数，``Toolkit`` 将把中断消息附加到中断发生时的 ``ToolResponse`` 上。
# 通过这种方式，智能体可以观察到工具在中断前返回的内容。
#
# 中断流式工具函数的示例如下：
#


async def streaming_function() -> AsyncGenerator[ToolResponse, None]:
    """一个可以被中断的流式工具函数。"""
    # 模拟一块响应
    yield ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="1234",
            ),
        ],
        stream=True,
    )

    # 模拟中断
    raise asyncio.CancelledError()

    # 由于取消，以下代码不会被执行
    yield ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="123456789",
            ),
        ],
    )


async def example_streaming_tool_interruption() -> None:
    """流式工具中断示例。"""
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
        print(f"块 {i}：")
        print(tool_response)
        print("中断标志：", tool_response.is_interrupted, "\n")
        i += 1


asyncio.run(example_streaming_tool_interruption())

# %%
# 自动工具管理
# -------------------------------------
# .. image:: https://img.alicdn.com/imgextra/i3/O1CN013cvRpO27MfesMsTeh_!!6000000007783-2-tps-840-521.png
#     :width: 100%
#     :align: center
#     :alt: 自动工具管理
#
#
# ``Toolkit`` 类通过引入 **工具组** （Group） 的概念，以及名为 ``reset_equipped_tools`` 的 **元工具函数** （Meta Tool） 来支持 **自动工具管理** 。
#
# 工具组是一组相关工具函数的集合，例如浏览器使用工具、地图服务工具等，它们将被一起管理。工具组有激活和非激活两种状态，
# 只有工具组被激活，其中的工具函数才对智能体可见，即可以通过 ``toolkit.get_json_schemas()`` 方法访问。
#
# 注意有一个名为 ``basic`` 的特殊组，它始终处于激活状态，注册工具时如果未指定组名，则工具函数将默认添加到此组。
#
# .. tip:: ``basic`` 组确保开发者不需要“组管理”的功能时，工具的基本使用不会受到影响。
#
# 现在我们尝试创建一个名为 ``browser_use`` 的工具组，其中包含一些网页浏览工具。
#


# 我们创建一些浏览器操作相关的工具
def navigate(url: str) -> ToolResponse:
    """导航到网页。

    Args:
        url (str):
            要导航到的网页的 URL。
    """
    pass


def click_element(element_id: str) -> ToolResponse:
    """点击网页上的元素。

    Args:
        element_id (str):
            要点击的元素的 ID。
    """
    pass


toolkit = Toolkit()

# 创建一个名为 browser_use 的工具组
toolkit.create_tool_group(
    group_name="browser_use",
    description="用于网页浏览的工具函数。",
    active=False,
    # 使用这些工具时的注意事项
    notes="""1. 使用 ``navigate`` 打开网页。
2. 当需要用户身份验证时，请向用户询问凭据
3. ...""",
)

toolkit.register_tool_function(navigate, group_name="browser_use")
toolkit.register_tool_function(click_element, group_name="browser_use")

# 我们也可以注册一些基本工具
toolkit.register_tool_function(execute_python_code)

# %%
# 此时 ``browser_use`` 未被激活，如果我们检查工具 JSON schema，只能看到 ``execute_python_code`` 工具：

print("此时对智能体可见的工具函数 JSON Schemas：")
print(json.dumps(toolkit.get_json_schemas(), indent=4, ensure_ascii=False))

# %%
# 使用 ``update_tool_groups`` 方法激活或停用工具组：

toolkit.update_tool_groups(group_names=["browser_use"], active=True)

print("激活后对智能体可见的工具函数 JSON Schemas：")
print(json.dumps(toolkit.get_json_schemas(), indent=4, ensure_ascii=False))

# %%
# 此外，``Toolkit`` 提供了一个名为 ``reset_equipped_tools`` 的元工具函数，它会将所有组名（除了 "basic"）作为一个 bool 型的参数，
# 让智能体调用该工具来决定要激活哪些工具组：
#
# .. note:: 在 ``ReActAgent`` 类的实现中，只需要在构造函数中将 ``enable_meta_tool`` 设置为 ``True`` 即可启用元工具函数。
#

# 注册元工具函数
toolkit.register_tool_function(toolkit.reset_equipped_tools)

reset_equipped = next(
    tool
    for tool in toolkit.get_json_schemas()
    if tool["function"]["name"] == "reset_equipped_tools"
)
print("``reset_equipped_tools`` 函数的 JSON schema：")
print(
    json.dumps(
        reset_equipped,
        indent=4,
        ensure_ascii=False,
    ),
)

# %%
# 当智能体调用 ``reset_equipped_tools`` 时，对应工具组将被激活，同时返回的结果中将包含工具的使用注意事项。
#


async def mock_agent_reset_tools() -> None:
    """模拟智能体调用 reset_equipped_tools 函数。"""
    res = await toolkit.call_tool_function(
        ToolUseBlock(
            type="tool_use",
            id="456",
            name="reset_equipped_tools",
            input={
                "browser_use": True,  # 激活浏览器使用工具组
            },
        ),
    )

    async for tool_response in res:
        print("工具响应中的文字返回：")
        print(tool_response.content[0]["text"])


asyncio.run(mock_agent_reset_tools())

# %%
# 此外，``Toolkit`` 还通过 ``get_activated_notes`` 函数提供已经被激活了的工具组的 notes，开发者也可以将其组装到智能体的系统提示中，从而达到动态管理工具的作用。
#
# .. tip:: 自动工具管理功能已在 ``ReActAgent`` 类中实现，有关更多详细信息，请参考 :ref:`agent` 部分。
#

# 再创建一个工具组
toolkit.create_tool_group(
    group_name="map_service",
    description="谷歌地图服务工具。",
    active=True,
    notes="""1. 使用 ``get_location`` 获取地点的位置。
2. ...""",
)

print("激活工具组的汇总注意事项：")
print(toolkit.get_activated_notes())

# %%
# 进一步阅读
# ---------------------
# - :ref:`agent`
# - :ref:`state`
# - :ref:`mcp`
#
