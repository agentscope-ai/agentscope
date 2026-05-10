# 第三十一章 为什么 ContentBlock 是 Union

在 `src/agentscope/message/_message_block.py` 中，七种内容块全部用 `TypedDict` 定义，最后用一个 `Union` 类型收束：

```python
ContentBlock = (
    ToolUseBlock
    | ToolResultBlock
    | TextBlock
    | ThinkingBlock
    | ImageBlock
    | AudioBlock
    | VideoBlock
)
```

没有基类。没有虚方法。没有 `isinstance` 检查。每个块只是一个带 `type` 字段的字典。这不是偷懒——这是一个经过权衡的设计决策。

## 一、决策回顾

### 1.1 TypedDict 的真实写法

打开 `_message_block.py`，从第 9 行开始：

```python
class TextBlock(TypedDict, total=False):
    type: Required[Literal["text"]]
    text: str

class ToolUseBlock(TypedDict, total=False):
    type: Required[Literal["tool_use"]]
    id: Required[str]
    name: Required[str]
    input: Required[dict[str, object]]
    raw_input: str

class ToolResultBlock(TypedDict, total=False):
    type: Required[Literal["tool_result"]]
    id: Required[str]
    output: Required[
        str | List[TextBlock | ImageBlock | AudioBlock | VideoBlock]
    ]
    name: Required[str]
```

每个类声明 `total=False`，表示大部分字段是可选的；只有标注了 `Required` 的字段必须存在。`type` 字段用 `Literal["text"]` 这样的精确字面量类型，让 mypy 能在编译期区分不同的块。

这些 TypedDict 类不是"类"——它们是类型注解。运行时 `TextBlock(type="text", text="hello")` 返回的是一个普通 `dict`：

```python
>>> isinstance(TextBlock(type="text", text="hello"), dict)
True
```

没有任何自定义行为附加在上面。

### 1.2 Union 的收束方式

第 110-118 行定义了联合类型：

```python
ContentBlock = (
    ToolUseBlock
    | ToolResultBlock
    | TextBlock
    | ThinkingBlock
    | ImageBlock
    | AudioBlock
    | VideoBlock
)
```

`Msg` 类（`_message_base.py` 第 27 行）用这个类型约束 content 字段：

```python
content: str | Sequence[ContentBlock]
```

在 `_message_base.py` 的 `to_dict` 方法（第 75 行）中，序列化直接交给 Python 的 dict 处理：

```python
def to_dict(self) -> dict:
    return {
        "id": self.id,
        "name": self.name,
        "role": self.role,
        "content": self.content,  # ContentBlock 已经是 dict
        "metadata": self.metadata,
        "timestamp": self.timestamp,
    }
```

没有 `to_json()`。没有 `serialize()`。没有自定义编解码器。`self.content` 直接进入返回的 dict，因为 TypedDict 在运行时就是 dict。

`from_dict`（第 87 行）同样直接——读 JSON 反序列化出的 dict 原样传入 `content` 参数，不需要任何转换逻辑。

### 1.3 实际使用中的类型区分

消费 ContentBlock 的代码全部通过 `block["type"]` 做分发。`_converter.py` 第 79-124 行是一个典型例子：

```python
def _convert_block_to_part(block: ContentBlock) -> Dict[str, Any] | None:
    block_type = block.get("type")
    if block_type == "text":
        part = {"type": "text", "content": block.get("text", "")}
    elif block_type == "tool_use":
        part = {"type": "tool_call", "id": block.get("id", ""),
                "name": block.get("name", ""),
                "arguments": block.get("input", {})}
    elif block_type in ("image", "audio", "video"):
        source = block.get("source", {})
        ...
```

`_formatter_base.py` 第 77-123 行的模式完全相同：遍历 block 列表，用 `block["type"]` 分流，然后直接用 `block["text"]`、`block["source"]` 等字段。没有多态分发，没有方法调用——全是字典取值。

## 二、被否方案

### 2.1 OOP 类层次

更"正统"的面向对象方案会这样设计：

```python
from abc import ABC, abstractmethod

class ContentBlockBase(ABC):
    """内容块基类。"""
    @abstractmethod
    def block_type(self) -> str: ...

    @abstractmethod
    def to_dict(self) -> dict: ...

    @classmethod
    def from_dict(cls, data: dict) -> "ContentBlockBase": ...

class TextBlockImpl(ContentBlockBase):
    def __init__(self, text: str):
        self.text = text

    def block_type(self) -> str:
        return "text"

    def to_dict(self) -> dict:
        return {"type": "text", "text": self.text}

class ToolUseBlockImpl(ContentBlockBase):
    def __init__(self, id: str, name: str, input: dict):
        self.id = id
        self.name = name
        self.input = input

    def block_type(self) -> str:
        return "tool_use"

    def to_dict(self) -> dict:
        return {"type": "tool_use", "id": self.id,
                "name": self.name, "input": self.input}
```

这个方案的吸引力在于：基类可以定义通用行为（序列化、校验、格式化），子类可以添加方法（比如 `ToolUseBlock.execute()`），`isinstance` 检查更自然，IDE 补全更友好。

### 2.2 dataclass 方案

另一个中间路线是用 `dataclass` 代替 TypedDict：

```python
from dataclasses import dataclass

@dataclass
class TextBlock:
    type: Literal["text"] = "text"
    text: str = ""

    def to_dict(self) -> dict:
        return {"type": self.type, "text": self.text}
```

dataclass 提供了 `__eq__`、`__repr__`、默认值等便利，但仍然需要手动 `to_dict` / `from_dict`，因为它不是原生 dict。

### 2.3 Pydantic 模型

用 Pydantic 可以同时获得验证和序列化：

```python
from pydantic import BaseModel

class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str

class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, object]
```

但 Pydantic 模型不是 dict——`json.dumps(block)` 不会直接工作，必须走 `.model_dump()` 或 `.model_dump_json()`。这给序列化路径增加了一层间接。

## 三、后果分析

### 3.1 收益

**零成本 JSON 序列化。** 这是 TypedDict 方案最直接的优势。看 `Msg.to_dict()`（第 75-84 行）：`self.content` 是一个 `list[dict]`（或 `str`），直接放入返回的 dict。`from_dict`（第 87-99 行）读 JSON 数据，`content` 字段原样传入构造函数。整条链路上没有任何 `serialize()`/`deserialize()` 调用。

当一个 LLM API 返回 JSON 时，`json.loads()` 的输出可以直接成为 `ContentBlock`。当一个 `Msg` 需要持久化到 Redis 或写入文件时，`json.dumps()` 直接生效。在 `to_dict` 中（第 81 行）：

```python
"content": self.content,
```

仅此一行。没有循环，没有映射，没有类型判断。

**类型安全不妥协。** TypedDict 配合 `Literal` 类型和 Union，让 mypy 能做精确的类型窄化（type narrowing）。看 `_message_base.py` 第 149-201 行的 `get_content_blocks` 方法——六个 `@overload` 签名精确对应六种 block 类型，调用者根据 `block_type` 参数得到正确的返回类型。mypy 知道 `get_content_blocks("tool_use")` 返回 `Sequence[ToolUseBlock]`，不是模糊的 `Sequence[ContentBlock]`。

`Literal["text"]` 标签让 mypy 在 `if block["type"] == "text":` 之后自动窄化类型，知道 `block` 此时是 `TextBlock`，可以安全访问 `block["text"]`。

**与 LLM API 的天然对齐。** OpenAI 的 tool_calls 返回 JSON。Anthropic 的 content blocks 是 JSON。Gemini 的 parts 是 JSON。所有主流 LLM API 的内容格式本质上都是带 `type` 字段的字典。TypedDict 是这些 JSON 结构在 Python 类型系统中的直接投影——不存在"领域模型"和"传输格式"之间的阻抗失配。

### 3.2 代价

**没有共享行为。** TypedDict 不能有方法。如果每种 block 需要一个 `display()` 方法或 `validate()` 方法，你只能在函数外部写：

```python
def display_block(block: ContentBlock) -> str:
    if block["type"] == "text":
        return block["text"]
    elif block["type"] == "tool_use":
        return f"调用 {block['name']}({block['input']})"
    ...
```

在 `_converter.py` 中，`_convert_block_to_part` 函数（第 57 行）做的就是这件事。它是一个约 50 行的 if-elif 链，逐个处理七种 block 类型。如果 block 有 `to_part()` 方法，这段代码可以分散到各个类中。但当前方案下，所有行为都集中在消费方。

**运行时类型安全有限。** TypedDict 在运行时就是 `dict`。`{"type": "text", "wrong_key": "oops"}` 在 mypy 眼中是类型错误，但在运行时不会被拦截。Python 不会在 `TextBlock(type="text", text="hi")` 时校验字段名——它只是构造一个普通 dict。如果输入来自不可信源（比如用户上传的 JSON），错误字段名只会在后续使用时才暴露。

**扩展需要修改 Union。** 添加第八种 block 类型（比如 `FileBlock`）意味着：定义新的 TypedDict、把它加入 `ContentBlock` Union、更新 `ContentBlockTypes` Literal、在所有消费 `ContentBlock` 的 if-elif 链中添加新分支。这是"开放-封闭原则"的反面——每次扩展都要修改已有代码。

### 3.3 实际影响评估

在 AgentScope 的代码中，消费 `ContentBlock` 的 if-elif 链只有五六处：`_converter.py` 的 `_convert_block_to_part`、`_formatter_base.py` 的格式化逻辑、`_agent_base.py` 的文本提取、几个 reader 的 block 处理。这些地方的总行数不超过 200 行。

相比之下，序列化/反序列化路径贯穿整个框架——从 API 响应到 `Msg`、从 `Msg` 到 Memory、从 Memory 到 Formatter。在这条路径上，TypedDict 的"零转换"特性每天节省的代码量和 bug 数量，远大于五六处 if-elif 链的维护成本。

## 四、横向对比

### 4.1 LangChain

LangChain 的 `AIMessage`、`HumanMessage` 等是 Pydantic `BaseModel` 的子类。tool_calls 存储为 `ToolCall` 对象列表，`ToolCall` 也是 BaseModel。序列化通过 `.model_dump()` 或 `.to_json()` 完成。

好处：Pydantic 提供字段验证、默认值、JSON Schema 生成。代价：每次序列化都需要显式转换，API 响应的 dict 必须先解析成 Pydantic 对象，持久化时再转回 dict。

### 4.2 Anthropic SDK

Anthropic 的 Python SDK 使用 dataclass 定义 content block（`TextBlock`、`ToolUseBlock` 等），配合 `@typing.final` 装饰器。序列化通过手写的 `_to_dict()` 方法完成。

好处：dataclass 的 `__init__` 参数有类型提示和默认值，IDE 体验好。代价：每个 block 类型都需要手写序列化逻辑，或者依赖统一的转换函数。

### 4.3 OpenAI SDK

OpenAI 的 Python SDK 使用 Pydantic V2 模型。`ChatCompletionMessageToolCall` 是 `BaseModel`，嵌套在 `ChatCompletionMessage` 中。序列化统一走 Pydantic 的 `.model_dump()`。

好处：与 Pydantic 生态深度集成，验证和 Schema 生成都免费。代价：与 AgentScope 类似，序列化路径需要显式调用转换方法。

### 4.4 对比总结

| 方案 | 运行时类型 | 序列化成本 | 类型安全 | 共享行为 |
|------|-----------|-----------|---------|---------|
| TypedDict + Union | dict | 零 | mypy 级 | 无 |
| Pydantic BaseModel | BaseModel 实例 | `.model_dump()` | 运行时 + 静态 | 有 |
| dataclass | dataclass 实例 | 手写 | mypy 级 | 有 |
| ABC + 子类 | 子类实例 | 手写 | mypy 级 | 有 |

AgentScope 选择了第一列。代价明确：没有共享行为，运行时不验证。收益同样明确：序列化零成本，与 LLM API 天然对齐。

## 五、你的判断

回顾 `_message_block.py` 的 129 行代码。七种 TypedDict，一个 Union，一个 Literal。整章讨论的核心问题是：

**当你的数据结构本身就是传输格式时，是否还需要一层对象封装？**

TypedDict 方案的前提假设是：ContentBlock 是"数据"而非"对象"。它们没有身份、没有生命周期、没有不变量需要维护。它们只是 LLM API 响应的 Python 投影，在组件之间传递时不需要任何转换。在这个前提下，封装层是纯开销。

但这个假设有边界。如果未来 ContentBlock 需要：
- 复杂的校验逻辑（比如 `ToolUseBlock.input` 的 schema 验证）
- 通用行为（比如 `render()` 方法适配不同终端）
- 不变量保护（比如创建后不可修改）

那 TypedDict 的字典本性就成了负担，而不是优势。

你可以问自己：在你见过的项目中，"数据"和"对象"的边界在哪里？TypedDict 方案在哪个规模点开始变得不合适？有没有一种混合方案——比如 TypedDict 定义数据结构，但用 Protocol 定义行为接口——能在两个世界之间取到更好的平衡？
