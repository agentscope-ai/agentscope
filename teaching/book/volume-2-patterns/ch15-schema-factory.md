# 第十五章：工厂与 Schema——从函数到 JSON Schema

**难度**：中等

> 你的工具函数有嵌套的 Pydantic 参数，Schema 生成报错了。错误信息是 `PydanticInvalidForJsonSchema`，说你的自定义类型无法序列化。你看了看代码，明明类型注解写得好好的，怎么 `create_model` 就不认呢？这一章我们拆解 AgentScope 如何把一个普通 Python 函数变成 LLM 能理解的 JSON Schema。

---

## 1. 开场场景

你写了一个查询天气的工具函数，参数里嵌套了一个 Pydantic 模型：

```python
from pydantic import BaseModel

class Location(BaseModel):
    """A geographic location."""
    city: str
    country: str

def search_weather(location: Location, unit: str = "celsius") -> str:
    """Search weather for a location.

    Args:
        location (Location):
            The target location to query.
        unit (str, optional):
            Temperature unit, defaults to "celsius".

    Returns:
        str: The weather information.
    """
    ...
```

注册到 Toolkit 时：

```python
toolkit = Toolkit()
toolkit.register_tool_function(search_weather)
```

如果 `Location` 类型的 JSON Schema 生成出了问题，LLM 就无法正确调用这个工具。AgentScope 内部用 `pydantic.create_model` 动态构建模型、用 `model_json_schema()` 生成 Schema——这个从 Python 函数到 JSON Schema 的转换过程，就是本章要拆解的工厂模式。

---

## 2. 设计模式概览

AgentScope 的工具 Schema 生成遵循一个三步流水线：

```
Python 函数
    |
    v
[inspect.signature]  提取参数名、类型注解、默认值
    |
    v
[pydantic.create_model]  动态构造 Pydantic 模型
    |
    v
[model.model_json_schema()]  生成 JSON Schema
    |
    v
JSON Schema (OpenAI function calling 格式)
```

工厂模式体现在 `create_model` 这一步：不是手写 JSON Schema，而是让 Pydantic 替你生成。这意味着所有 Pydantic 支持的类型（嵌套 BaseModel、Optional、Union、list、Literal 等）都能自动处理。

核心函数是 `_parse_tool_function`，定义在 `src/agentscope/_utils/_common.py` 第 339-455 行。调用链如下：

```
Toolkit.register_tool_function()     (_toolkit.py 第 274 行)
    |
    v
_parse_tool_function(tool_func)      (_common.py 第 339 行)
    |
    v
create_model("_StructuredOutputDynamicClass", **fields)  (_common.py 第 434 行)
    |
    v
base_model.model_json_schema()       (_common.py 第 439 行)
    |
    v
_remove_title_field(schema)          (_common.py 第 442 行)
```

---

## 3. 源码分析

### 3.1 解析 docstring：提取函数描述和参数说明

`_parse_tool_function` 的前半段（第 362-373 行）处理 docstring：

```python
docstring = parse(tool_func.__doc__)
params_docstring = {_.arg_name: _.description for _ in docstring.params}

# Function description
descriptions = []
if docstring.short_description is not None:
    descriptions.append(docstring.short_description)

if include_long_description and docstring.long_description is not None:
    descriptions.append(docstring.long_description)

func_description = "\n".join(descriptions)
```

这里用的是 `docstring_parser` 库（第 18 行 `from docstring_parser import parse`），它能把 Google 风格的 docstring 解析成结构化数据。`params_docstring` 是一个字典，键是参数名，值是参数描述。这些描述最终会成为 JSON Schema 中每个字段的 `description` 属性。

注意 `include_long_description` 参数：有些工具函数的 docstring 很长（包含使用示例、注意事项等），全部放进 Schema 会浪费 token。`register_tool_function` 的默认值是 `True`（第 284 行），调用者可以按需关闭。

### 3.2 遍历函数签名：构建字段定义

第 376-432 行是核心逻辑——遍历函数参数，为每个参数构造 Pydantic `Field` 定义：

```python
fields = {}
for name, param in inspect.signature(tool_func).parameters.items():
    if name in ["self", "cls"]:
        continue
```

`inspect.signature()` 拿到函数的完整签名信息，包括参数名、类型注解、默认值、参数种类（位置/可变位置/可变关键字）。

接下来按参数种类分三种情况处理：

**普通参数**（第 421-432 行）：

```python
else:
    fields[name] = (
        Any
        if param.annotation == inspect.Parameter.empty
        else param.annotation,
        Field(
            description=params_docstring.get(name, None),
            default=...
            if param.default is param.empty
            else param.default,
        ),
    )
```

`fields[name]` 是一个元组 `(type_annotation, Field(...))`——这是 `pydantic.create_model` 要求的字段定义格式。注意两个细节：
- 没有类型注解时降级为 `Any`
- 没有默认值时用 `...`（Pydantic 的 `Ellipsis`，表示必填字段）

**可变位置参数 `*args`**（第 402-419 行）：

```python
elif param.kind == inspect.Parameter.VAR_POSITIONAL:
    if not include_var_positional:
        continue

    fields[name] = (
        list[Any]
        if param.annotation == inspect.Parameter.empty
        else list[param.annotation],  # type: ignore
        Field(
            description=params_docstring.get(
                f"*{name}",
                params_docstring.get(name, None),
            ),
            default=[]
            if param.default is param.empty
            else param.default,
        ),
    )
```

`*args` 被映射为 `list` 类型——这在语义上合理，因为可变位置参数本质上就是一个列表。注意 docstring 中的参数名可能写成 `*args`，所以查找描述时先尝试 `f"*{name}"`，再回退到 `name`。默认不包含 `*args`（`include_var_positional` 默认 `False`）。

**可变关键字参数 `**kwargs`**（第 383-400 行）：

```python
if param.kind == inspect.Parameter.VAR_KEYWORD:
    if not include_var_keyword:
        continue

    fields[name] = (
        Dict[str, Any]
        if param.annotation == inspect.Parameter.empty
        else Dict[str, param.annotation],
        Field(
            description=params_docstring.get(
                f"**{name}",
                params_docstring.get(name, None),
            ),
            default={}
            if param.default is param.empty
            else param.default,
        ),
    )
```

`**kwargs` 被映射为 `Dict[str, Any]`。同理，默认不包含（`include_var_keyword` 默认 `False`）。

### 3.3 create_model：动态工厂

第 434-438 行，用 Pydantic 的 `create_model` 工厂函数动态创建模型：

```python
base_model = create_model(
    "_StructuredOutputDynamicClass",
    __config__=ConfigDict(arbitrary_types_allowed=True),
    **fields,
)
```

`create_model` 是 Pydantic 提供的运行时模型构造函数。它接收模型名和字段定义，返回一个新的 `BaseModel` 子类。这里有两个要点：

**1. 模型名无所谓。** `"_StructuredOutputDynamicClass"` 是固定的，因为这个名字不会出现在生成的 JSON Schema 中（后面会用 `_remove_title_field` 移除 `title` 字段）。

**2. `arbitrary_types_allowed=True`。** 这是关键配置。当你的函数参数有自定义类型（比如另一个 Pydantic 模型、枚举、或者非标准类型）时，Pydantic 默认会拒绝。开启这个选项后，Pydantic 会尽量把这些类型转成 JSON Schema——如果类型本身是 `BaseModel` 子类，它递归生成嵌套 Schema；如果是无法序列化的类型，才会抛出异常。

### 3.4 生成并清理 JSON Schema

第 439-455 行：

```python
params_json_schema = base_model.model_json_schema()

# Remove the title from the json schema
_remove_title_field(params_json_schema)

func_json_schema: dict = {
    "type": "function",
    "function": {
        "name": tool_func.__name__,
        "parameters": params_json_schema,
    },
}

if func_description not in [None, ""]:
    func_json_schema["function"]["description"] = func_description

return func_json_schema
```

`model_json_schema()` 是 Pydantic v2 的方法，返回标准的 JSON Schema 字典。然后用 `_remove_title_field`（第 239-263 行）递归移除所有 `title` 字段。

为什么要移除 `title`？因为 Pydantic 自动给每个 Schema 节点加 `title`（比如 `"title": "_StructuredOutputDynamicClass"`、`"title": "Location"`）。这些 `title` 对 LLM 来说是噪音——它们描述的是 Python 类型名，不是工具的语义信息，反而可能误导 LLM。

最终输出的格式遵循 OpenAI function calling 规范：

```json
{
    "type": "function",
    "function": {
        "name": "search_weather",
        "description": "Search weather for a location.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "$defs": { "Location": { ... } },
                    "allOf": [{ "$ref": "#/$defs/Location" }]
                },
                "unit": {
                    "type": "string",
                    "default": "celsius",
                    "description": "Temperature unit, defaults to \"celsius\"."
                }
            },
            "required": ["location"]
        }
    }
}
```

### 3.5 _remove_title_field：递归清理

`_remove_title_field`（第 239-263 行）是一个递归函数，遍历 JSON Schema 的四个位置：

```python
def _remove_title_field(schema: dict) -> None:
    if "title" in schema:
        schema.pop("title")
    if "properties" in schema:
        for prop in schema["properties"].values():
            if isinstance(prop, dict):
                _remove_title_field(prop)
    if "items" in schema and isinstance(schema["items"], dict):
        _remove_title_field(schema["items"])
    if "additionalProperties" in schema and isinstance(
        schema["additionalProperties"], dict,
    ):
        _remove_title_field(schema["additionalProperties"])
```

它覆盖了 JSON Schema 中可能包含 `title` 的所有位置：顶层、`properties` 内的每个属性、数组 `items`、以及 `additionalProperties`。这个函数在多处被调用——不仅 `_parse_tool_function` 用它，`RegisteredToolFunction.extended_json_schema` 属性（`_types.py` 第 74、103 行）也会用它来清理动态扩展的 Schema。

### 3.6 register_tool_function：Schema 的后处理

`_parse_tool_function` 生成的 Schema 在 `register_tool_function`（`_toolkit.py` 第 274 行）中还要经过几步后处理。

**第一步：选择 Schema 来源**（第 416-425 行）。普通函数直接调用 `_parse_tool_function`：

```python
else:
    input_func_name = tool_func.__name__
    original_func = tool_func
    json_schema = json_schema or _parse_tool_function(
        tool_func,
        include_long_description=include_long_description,
        include_var_positional=include_var_positional,
        include_var_keyword=include_var_keyword,
    )
```

注意 `json_schema or ...` 的写法：如果调用者手动提供了 `json_schema`（第 281 行参数），就跳过自动生成。这给开发者留了 escape hatch——当自动生成的 Schema 不满意时，可以手写。

**第二步：移除 preset_kwargs**（第 440-459 行）。`preset_kwargs` 是调用者预设的参数值——这些参数不会暴露给 LLM（比如 API key），需要从 Schema 中删除：

```python
for arg_name in preset_kwargs or {}:
    if arg_name in json_schema["function"]["parameters"]["properties"]:
        json_schema["function"]["parameters"]["properties"].pop(arg_name)

if "required" in json_schema["function"]["parameters"]:
    for arg_name in preset_kwargs or {}:
        if arg_name in json_schema["function"]["parameters"]["required"]:
            json_schema["function"]["parameters"]["required"].remove(arg_name)

    if len(json_schema["function"]["parameters"]["required"]) == 0:
        json_schema["function"]["parameters"].pop("required", None)
```

这不仅是删除属性，还同步清理 `required` 数组——如果预设参数是必填的，移除后 `required` 里也不能再有它。如果 `required` 变空了，整个字段都要删掉。

**第三步：构造 RegisteredToolFunction**（第 461-473 行）。Schema 和其他元信息被打包成 `RegisteredToolFunction` dataclass，存入 `self.tools` 字典。

### 3.7 RegisteredToolFunction：Schema 的动态扩展

`RegisteredToolFunction`（`_types.py` 第 16 行）不仅保存 Schema，还支持运行时扩展。看 `extended_json_schema` 属性（第 62-132 行）：

```python
@property
def extended_json_schema(self) -> dict:
    if self.extended_model is None:
        return self.json_schema

    extended_schema = self.extended_model.model_json_schema()
    merged_schema = deepcopy(self.json_schema)
    _remove_title_field(merged_schema)

    for key, value in extended_schema["properties"].items():
        if key in self.json_schema["function"]["parameters"]["properties"]:
            raise ValueError(
                f"The field `{key}` already exists in the original "
                f"function schema of `{self.name}`."
            )
        merged_schema["function"]["parameters"]["properties"][key] = value
    ...
```

`extended_model` 是一个额外的 Pydantic `BaseModel`，可以在运行时注入新字段。`Toolkit.get_json_schemas()`（`_toolkit.py` 第 558 行）中就用这个机制动态生成 `reset_equipped_tools` 工具的参数——每个工具组对应一个 `bool` 字段。

这个属性还处理了嵌套模型中的 `$defs` 合并（第 94-130 行）：如果扩展模型引入了新的 `$defs`（Pydantic 对嵌套模型的 Schema 引用），它会检测冲突并合并。

---

## 4. 设计一瞥

### 为什么用 Pydantic 而不是手写 Schema？

一个直接的想法是遍历函数参数，手动构造 `{"type": "string", "description": "..."}` 这样的字典。AgentScope 选择先走 Pydantic 再转 JSON Schema，原因有三：

**1. 类型系统复用。** Pydantic 已经理解 Python 的类型注解系统——`Optional[str]` 变成 `{"anyOf": [{"type": "string"}, {"type": "null"}]}`，`list[int]` 变成 `{"type": "array", "items": {"type": "integer"}}`，嵌套 `BaseModel` 变成 `$ref` 引用。手写这些转换逻辑不仅重复，而且容易出错。

**2. 验证能力。** Pydantic 生成的 Schema 天然具备验证能力。虽然 `_parse_tool_function` 本身不做验证，但 `create_model` 在构造时就会检查类型是否合法——如果传入无法序列化的类型，在注册阶段就会报错，而不是等到 LLM 调用时才发现。

**3. 嵌套类型的递归处理。** 当参数类型是另一个 `BaseModel` 时（如开场的 `Location`），Pydantic 递归生成子 Schema 并放到 `$defs` 里。手写的话，你需要自己维护一个类型注册表来处理嵌套引用。

### 为什么不在注册时直接验证 LLM 输入？

`_parse_tool_function` 生成的 Schema 只用于告诉 LLM "你应该返回什么格式的参数"。LLM 返回的 JSON 字符串由 Formatter 层解析，不一定经过 Pydantic 验证。这是刻意的设计选择——Schema 的职责是"描述"，不是"验证"。验证发生在工具函数被实际调用时（Python 原生的类型匹配和函数签名检查）。

---

## 5. 横向对比

其他框架如何从函数生成工具 Schema？

**LangChain** 使用 `@tool` 装饰器，内部调用 `create_schema_from_function`（LangChain 自己的实现，不依赖 Pydantic 的 `create_model`）。它解析类型注解和 docstring 的方式类似，但手写了 `Optional`、`Union`、`Enum` 等类型的 JSON Schema 转换。好处是不依赖 Pydantic，坏处是每增加一种类型都需要手动适配。

**OpenAI 官方 SDK** 的 `parse_tool` 函数同样使用 Pydantic 的 `model_json_schema`，但要求工具参数整体是一个 `BaseModel`（即 `def foo(params: MyParamsModel)`），而不是逐参数注解。这种方式更严格，但不支持混合类型注解和 `*args`/`**kwargs`。

**Anthropic SDK** 的 `tool` 装饰器要求手写 `input_schema` 字典，不做自动推断。最灵活，但开发者负担最大。

**AgentScope** 的方式介于 LangChain 和 OpenAI 之间：用 Pydantic 的 `create_model` 做工厂，但不要求参数是 `BaseModel`——普通类型注解也行。这让开发者可以用最自然的方式写工具函数，同时享受 Pydantic 的类型转换能力。

---

## 6. 调试实践

当 Schema 生成出错时，按以下步骤排查：

**Step 1：检查函数签名。** `_parse_tool_function` 用 `inspect.signature` 提取参数。如果你的函数用了 `functools.partial`，签名可能已经被改变。检查方法：

```python
import inspect
print(inspect.signature(my_tool_function))
# 确认参数名、类型注解、默认值都正确
```

**Step 2：单独运行 create_model。** 如果 `register_tool_function` 报 Pydantic 错误，手动复现问题：

```python
from pydantic import create_model, Field, ConfigDict
from typing import Any

# 模拟 _parse_tool_function 的逻辑
fields = {
    "location": (Location, Field(description="The target location.")),
    "unit": (str, Field(default="celsius", description="Temperature unit.")),
}
model = create_model(
    "TestModel",
    __config__=ConfigDict(arbitrary_types_allowed=True),
    **fields,
)
print(model.model_json_schema())
```

如果这里报错，问题在类型定义。如果输出正常，问题在后续处理。

**Step 3：检查嵌套类型。** 开场场景的 `Location` 是嵌套 `BaseModel`，Pydantic 会生成 `$defs` 引用。如果你的自定义类型不是 `BaseModel` 子类（比如普通的 dataclass 或自定义类），需要确保 Pydantic 能序列化它。`arbitrary_types_allowed=True` 允许注册，但不保证能生成有意义的 JSON Schema。

**Step 4：检查 docstring 格式。** `docstring_parser` 要求 Google 风格的 docstring：

```python
# 正确格式
def foo(x: int, y: str) -> str:
    """Short description.

    Args:
        x (int): The x value.     # 注意：参数名、类型、冒号、描述
        y (str): The y value.
    """
```

如果格式不对，`params_docstring` 会是空字典，生成的 Schema 就没有参数描述。

**Step 5：检查 preset_kwargs。** 如果注册后某些参数"消失"了，检查是不是 `preset_kwargs` 把它们从 Schema 中移除了：

```python
toolkit.register_tool_function(
    my_func,
    preset_kwargs={"api_key": "sk-xxx"},  # api_key 不会出现在 Schema 里
)
```

---

## 7. 检查点

1. `_parse_tool_function` 的输入是什么类型？输出是什么格式？
2. `create_model` 的 `__config__=ConfigDict(arbitrary_types_allowed=True)` 解决了什么问题？如果去掉会怎样？
3. `_remove_title_field` 为什么要递归处理？Schema 中哪些位置可能包含 `title`？
4. `*args` 和 `**kwargs` 在 `_parse_tool_function` 中分别被映射成什么 JSON Schema 类型？默认是否包含？
5. `register_tool_function` 中的 `preset_kwargs` 参数有什么作用？它如何影响最终生成的 Schema？
6. `RegisteredToolFunction.extended_json_schema` 属性解决了什么问题？它如何处理 `$defs` 冲突？

---

## 8. 下一章预告

Schema 生成好了，但 LLM 不一定按 Schema 返回——下一章看 AgentScope 的消息管道如何处理流式 JSON 解析和工具调用的渐进式重构。
