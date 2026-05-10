# 第二十八章 为什么不用装饰器注册工具

在 Python 的工具注册领域，装饰器是事实上的标准写法。LangChain 用 `@tool`，OpenAI Agents SDK 用 `@function_tool`，连 Anthropic 的 SDK 都在用 `@beta_tool`。但 AgentScope 选择了不同的道路：显式的 `register_tool_function()` 调用。这不是遗漏，而是一个有意识的设计决策。

## 一、决策回顾

工具注册的入口在 `src/agentscope/tool/_toolkit.py` 第 274 行。完整的方法签名如下：

```python
def register_tool_function(
    self,
    tool_func: ToolFunction,
    group_name: str | Literal["basic"] = "basic",
    preset_kwargs: dict[str, JSONSerializableObject] | None = None,
    func_name: str | None = None,
    func_description: str | None = None,
    json_schema: dict | None = None,
    include_long_description: bool = True,
    include_var_positional: bool = False,
    include_var_keyword: bool = False,
    postprocess_func: ... | None = None,
    namesake_strategy: Literal["override", "skip", "raise", "rename"] = "raise",
    async_execution: bool = False,
) -> None:
```

12 个参数。这不是代码膨胀——每个参数都服务于一个真实的场景。让我们逐一追踪装饰器方案难以覆盖的能力。

**分组管理（第 277 行 `group_name`）**：工具可以属于不同的组，只有活跃组的工具才会被包含在 JSON schema 中。第 615-619 行的 `get_json_schemas` 方法只返回 "basic" 组或活跃组的工具：

```python
return [
    tool.extended_json_schema
    for tool in self.tools.values()
    if tool.group == "basic" or self.groups[tool.group].active
]
```

同一个 Toolkit 里可以有几十个工具，但按需激活。装饰器在函数定义时执行，此时你还没有组的概念。

**预置参数（第 278 行 `preset_kwargs`）**：某些参数是开发者预设的，不应该暴露给 Agent。第 441-459 行的逻辑会在生成 JSON schema 时移除这些参数。例如，一个搜索工具的 `api_key` 参数应该由开发者注入，而非让 Agent 填写。第 441 行：

```python
for arg_name in preset_kwargs or {}:
    if arg_name in json_schema["function"]["parameters"]["properties"]:
        json_schema["function"]["parameters"]["properties"].pop(arg_name)
```

装饰器做这件事需要引入 `@tool(preset_kwargs={...})` 语法，但装饰器的参数传递能力有限。

**重名策略（第 296-301 行 `namesake_strategy`）**：当两个工具同名时有四种处理方式：抛异常、覆盖、跳过、自动重命名。第 497-524 行的重命名逻辑使用 `shortuuid` 生成后缀。这个策略必须在注册时决定，而非函数定义时——你无法在写函数的时候预知未来会不会有同名冲突。

**MCP 工具兼容（第 384-388 行）**：`register_tool_function` 不仅接受普通函数，还接受 `MCPToolFunction` 对象和 `partial` 函数。MCP 工具从外部服务器动态获取，根本不存在源码文件，更不可能有装饰器。

**后处理函数（第 285-295 行 `postprocess_func`）**：工具执行后的后处理钩子，可以修改工具的返回值。第 918-929 行的逻辑将 `postprocess_func` 与具体的 `tool_call` 绑定。这是运行时才能确定的行为。

Schema 解析本身也值得注意。`_parse_tool_function` 定义在 `src/agentscope/_utils/_common.py` 第 339 行，它从函数签名和 docstring 自动生成 JSON schema。第 362-363 行用 `docstring_parser` 解析 docstring：

```python
docstring = parse(tool_func.__doc__)
params_docstring = {_.arg_name: _.description for _ in docstring.params}
```

第 377-432 行遍历函数参数，用 `pydantic.create_model` 动态创建模型（第 434 行）。这个过程是纯函数式的：输入一个函数，输出一个 JSON schema。它不依赖任何全局状态，不修改函数本身。这意味着同一个函数可以被多次解析，每次使用不同的配置参数（如 `include_long_description`、`include_var_positional`）。

## 二、被否方案

装饰器注册的典型实现是这样的：

```python
@tool(name="google_search", description="Search on Google")
def google_search(query: str, api_key: str) -> ToolResponse:
    """Search the web.

    Args:
        query: The search query.
        api_key: The API key.
    """
    # ...
```

更高级的装饰器还能自动推断 schema：

```python
@tool
def google_search(query: str) -> ToolResponse:
    """Search the web."""
    # 装饰器自动从类型注解和 docstring 生成 JSON schema
```

这种方式的卖点是简洁。函数定义和注册合二为一，减少了样板代码。但它有一个根本性的限制：注册时机被绑死在模块导入时。

考虑 AgentScope 真实面对的动态场景。MCP 客户端的注册（第 1035-1178 行的 `register_mcp_client`）在连接到外部服务器后，动态获取工具列表并逐个调用 `register_tool_function`。第 1166 行：

```python
self.register_tool_function(
    tool_func=func_obj,
    group_name=group_name,
    preset_kwargs=preset_kwargs,
    postprocess_func=postprocess_func,
    namesake_strategy=namesake_strategy,
)
```

这个 `func_obj` 来自第 1155 行的 `mcp_client.get_callable_function()`——运行时才从远程服务器获取的可调用对象。它没有 Python 源码，没有 `@tool` 装饰器，甚至没有 docstring。如果使用装饰器方案，MCP 工具的注册需要一个完全独立的路径。

再看配置驱动的注册：

```python
# 用户通过 API 提交一个新的工具
user_tool_config = {
    "name": "custom_search",
    "group": "search_tools",
    "preset_kwargs": {"api_key": get_api_key()},
    "namesake_strategy": "rename",
}

toolkit.register_tool_function(
    tool_func=user_submitted_function,
    **user_tool_config,
)
```

这种运行时注册在装饰器方案中需要大量的适配代码。

装饰器方案还有另一个隐藏问题：全局状态。`@tool` 通常需要一个全局的注册表来存储工具定义。看 LangChain 的实现——`@tool` 装饰器返回一个 `BaseTool` 实例，工具管理是分散的。如果两个 Agent 需要同一个函数但使用不同的配置（不同的 `preset_kwargs`），装饰器方案要么需要 `functools.partial` 包装，要么需要为每个 Agent 创建独立的装饰版本。

## 三、后果分析

### 3.1 好处：运行时灵活性

`register_tool_function` 是一个普通方法调用，可以在任何时机、任何上下文中执行。`register_mcp_client` 本身有 10 个参数（第 1035-1059 行），包括 `enable_funcs`、`disable_funcs`、`preset_kwargs_mapping`、`execution_timeout`。这些参数控制的是批量注册的行为，不是单个函数的元数据。装饰器方案无法表达这种"批量注册 + 过滤 + 映射"的语义。

### 3.2 好处：注册和定义的解耦

函数可以在一个模块里定义，在另一个模块里注册。同一个函数可以被注册到不同的 Toolkit 实例，使用不同的 `group_name` 和 `preset_kwargs`：

```python
# 工具函数定义（与注册无关）
def search(query: str, api_key: str) -> ToolResponse:
    ...

# 在 Agent A 的工具箱中注册（使用 A 的密钥和搜索组）
toolkit_a.register_tool_function(search, preset_kwargs={"api_key": key_a}, group_name="search")

# 在 Agent B 的工具箱中注册（使用 B 的密钥，basic 组）
toolkit_b.register_tool_function(search, preset_kwargs={"api_key": key_b})
```

装饰器将函数绑定到全局注册表，无法实现这种实例级别的差异化配置。

### 3.3 好处：可测试性

`_parse_tool_function`（第 339 行）是一个纯函数：输入一个函数，输出一个 JSON schema。它不修改函数本身，不依赖任何全局状态。你可以直接测试它：

```python
def my_func(a: int, b: str = "hello") -> None:
    """A test function.

    Args:
        a: The first arg.
        b: The second arg.
    """

schema = _parse_tool_function(my_func, include_long_description=False, ...)
assert schema["function"]["name"] == "my_func"
```

装饰器会修改函数对象本身（替换为 `BaseTool` 实例或包装函数），这使得测试更加复杂。你需要区分"原始函数"和"装饰后的工具对象"。

### 3.4 代价：显式的样板代码

每个工具注册需要一个方法调用。如果注册十几个工具，代码会比较冗长。装饰器方案的拥护者会指出，`@tool` 一行就能完成注册，而 `register_tool_function` 需要至少一行函数调用。

此外，`register_tool_function` 的 12 个参数（第 274-303 行）让方法签名变得很重。虽然大多数参数有默认值，但新用户面对这么长的参数列表可能会感到困惑。

### 3.5 代价：不够 Pythonic

Python 社区的习惯是"用装饰器标记函数的特殊用途"——`@property`、`@staticmethod`、`@app.route`、`@pytest.fixture`。`@tool` 延续了这个传统。显式调用 `register_tool_function` 打破了这个直觉，新用户需要阅读文档才能找到注册方式。

## 四、横向对比

**LangChain** 使用 `@tool` 装饰器。这是最主流的方案。装饰器从函数签名和 docstring 自动推断 JSON schema，返回一个 `BaseTool` 实例。LangChain 还支持 `StructuredTool.from_function` 类方法用于更复杂的场景——这本质上就是 AgentScope 的 `register_tool_function`，只是以类方法的形式呈现。LangChain 的折中意味着两套注册路径需要维护。

**OpenAI Agents SDK** 使用 `@function_tool` 装饰器。与 LangChain 类似，装饰器自动从类型注解生成 schema。但它支持 `name_override` 参数来覆盖函数名，以及 `RunContextWrapper` 参数来注入上下文。这些扩展参数表明装饰器方案在面对复杂需求时也需要参数化——装饰器的括号里开始堆积配置。OpenAI 的方案同样将工具绑定到装饰时，动态注册需要绕过装饰器。

**Anthropic SDK** 采用最原始的方式：手动编写 JSON schema 字典。没有装饰器，没有自动推断。工具定义就是一个包含 `name`、`description`、`input_schema` 的字典。这给了开发者完全的控制权，但也意味着每个工具都需要手写 schema。值得注意的是，Anthropic 最近引入了 `@beta_tool` 装饰器作为"beta"功能，从函数签名自动生成 schema——这说明即使是 SDK 提供者也在向装饰器方向移动，但核心 API 仍然是手动的。

AgentScope 的显式注册处于 LangChain/OpenAI 的装饰器方案和 Anthropic 的手动 schema 方案之间。它用 `_parse_tool_function` 自动生成 schema（避免手写的繁琐），但用显式的方法调用注册（避免装饰器的时机限制）。当框架需要支持 MCP、分组、预置参数、后处理、异步执行等高级特性时，装饰器的表达力不够。

## 五、你的判断

装饰器注册和显式注册，本质上是一个"约定优于配置"vs"显式优于隐式"的选择。

如果你的工具集是固定的、简单的（比如 5-10 个内部工具，不需要分组），装饰器方案的简洁性无可辩驳。如果你的工具集是动态的、来源多样的（本地函数、MCP 服务、用户提交），显式注册是唯一可行的方案。

一个可能的混合方案是提供装饰器语法糖，内部调用 `register_tool_function`：

```python
# 混合方案：装饰器作为便捷语法糖
@toolkit.tool(group="search", preset_kwargs={"api_key": key})
def search(query: str) -> ToolResponse:
    """Search the web."""
```

但这要求 `toolkit` 实例在装饰时已存在，又把注册时机绑回了初始化阶段。如果 `toolkit` 是在运行时动态创建的（比如根据配置文件），装饰器就无能为力了。

AgentScope 的选择暗示了它的目标场景：企业级多 Agent 系统，需要动态工具管理、权限控制和多源集成。在这个场景下，显式注册不是冗余，而是必要。

但反过来问：是否可以为简单场景提供一个轻量的装饰器入口，同时保留显式注册处理复杂场景？这两条路径的边界条件是什么——具体来说，当装饰器注册的工具和显式注册的工具在同一个 Toolkit 中共存时，冲突处理策略应该如何统一？
