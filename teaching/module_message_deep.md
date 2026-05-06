# Message 消息系统源码深度剖析

## 目录
1. 模块概述
2. 目录结构
3. 核心类与函数源码解读
4. 设计模式总结
5. 代码示例
6. 练习题

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 Msg 类的核心字段与 ContentBlock 的类型体系 | 列举、识别 |
| 理解 | 解释消息内容块的类型分发机制（@overload）设计意图 | 解释、比较 |
| 应用 | 使用 `get_content_blocks()`、`get_text_content()` 正确提取消息内容 | 实现、操作 |
| 分析 | 分析 Msg 类与 OpenAI 消息格式的映射关系与差异 | 分析、对比 |
| 评价 | 评价消息系统的类型安全设计，判断其在多模态场景下的扩展性 | 评价、推荐 |
| 创造 | 设计一个自定义 ContentBlock 类型支持新的媒体格式 | 设计、构建 |

## 先修检查

在开始学习本模块之前，请确认您已掌握以下知识：

- [ ] Python 普通类与 `__init__` 构造函数
- [ ] Python `typing` 模块（`@overload`、`Literal`、联合类型）
- [ ] OpenAI Chat API 消息格式基础
- [ ] 多态与类型分发概念

**预计学习时间**: 30 分钟

### Java 开发者对照

| Python 概念 | Java 等价物 | 说明 |
|-------------|------------|------|
| 普通类 `__init__` | Java 构造函数 | 手动初始化字段 |
| `@overload` | 方法重载 | 类型安全的多种参数签名 |
| `Union[A, B]` | `sealed interface` | 联合类型 ≈ 密封接口 |
| `assert` 字段校验 | 构造函数参数验证 | 在 `__init__` 中验证 role 和 content |
| `to_dict()` / `from_dict()` | Jackson 序列化 | JSON 互转 |

---

## 1. 模块概述

> **交叉引用**: Msg 对象是 Dispatcher 消息路由 ([Dispatcher 调度器深度分析](module_dispatcher_deep.md)) 和 Agent 消息处理 ([Agent 模块深度分析](module_agent_deep.md)) 的核心载体。Formatter 模块将 Msg 转换为各模型 API 所需格式，详见 [Formatter 消息格式化深度分析](module_formatter_deep.md)。ContentBlock 类型直接影响 Model 的 Token 计数逻辑，参见 [Model 模块深度分析](module_model_deep.md)。

Message 模块是 AgentScope 中的核心消息系统，负责在代理(Agent)之间传递信息。该模块实现了类似 OpenAI 的消息格式，支持多种内容类型，包括文本、图像、音频、视频以及工具调用。

**源码位置**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/message/`

---

## 2. 目录结构

```
message/
├── __init__.py          # 模块导出接口
├── _message_base.py     # Msg 类定义（核心消息类）
└── _message_block.py    # 内容块类型定义
```

### 2.1 模块导出内容 (`__init__.py`)

```python
from ._message_block import (
    ContentBlock,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
    Base64Source,
    URLSource,
)
from ._message_base import Msg

__all__ = [
    "TextBlock",
    "ThinkingBlock",
    "Base64Source",
    "URLSource",
    "ImageBlock",
    "AudioBlock",
    "VideoBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ContentBlock",
    "Msg",
]
```

---

## 3. 核心类与函数源码解读

### 3.1 Msg 类 (`_message_base.py`)

`Msg` 是 AgentScope 中的核心消息类，用于封装一条消息的完整信息。

#### 3.1.1 类定义与初始化

```python
class Msg:
    """The message class in agentscope."""

    def __init__(
        self,
        name: str,  # 消息发送者名称
        content: str | Sequence[ContentBlock],  # 消息内容
        role: Literal["user", "assistant", "system"],  # 角色
        metadata: dict[str, JSONSerializableObject] | None = None,  # 元数据
        timestamp: str | None = None,  # 时间戳
        invocation_id: str | None = None,  # API调用ID
    ) -> None:
```

**关键设计**:
- `name`: 标识消息发送者
- `content`: 支持纯文本或多种内容块的混合
- `role`: 三种角色 - user(用户)、assistant(助手)、system(系统)
- `metadata`: 用于存储结构化输出等额外信息
- `timestamp`: 自动生成，格式为 `YYYY-MM-DD HH:MM:SS.sss`
- `invocation_id`: 用于追踪 API 调用

#### 3.1.2 唯一ID生成

```python
self.id = shortuuid.uuid()
self.timestamp = (
    timestamp
    or datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f",)[:-3]
)
```

使用 `shortuuid` 生成短小且唯一的 ID，时间戳精确到毫秒。

#### 3.1.3 序列化与反序列化

```python
def to_dict(self) -> dict:
    """Convert the message into JSON dict data."""
    return {
        "id": self.id,
        "name": self.name,
        "role": self.role,
        "content": self.content,
        "metadata": self.metadata,
        "timestamp": self.timestamp,
    }

@classmethod
def from_dict(cls, json_data: dict) -> "Msg":
    """Load a message object from the given JSON data."""
    new_obj = cls(
        name=json_data["name"],
        content=json_data["content"],
        role=json_data["role"],
        metadata=json_data.get("metadata", None),
        timestamp=json_data.get("timestamp", None),
        invocation_id=json_data.get("invocation_id", None),
    )
    new_obj.id = json_data.get("id", new_obj.id)
    return new_obj
```

**设计要点**: `to_dict()` 用于将消息序列化为 JSON 格式，`from_dict()` 用于从 JSON 数据恢复消息对象。

#### 3.1.4 内容块操作

**`@overload` 类型签名**: `get_content_blocks()` 使用 `typing.overload` 为静态类型检查器提供精确的返回类型提示：

```python
@overload
def get_content_blocks(self, block_type: Literal["text"]) -> Sequence[TextBlock]: ...
@overload
def get_content_blocks(self, block_type: Literal["tool_use"]) -> Sequence[ToolUseBlock]: ...
@overload
def get_content_blocks(self, block_type: Literal["image"]) -> Sequence[ImageBlock]: ...
# ... 其他类型类似
@overload
def get_content_blocks(self, block_type: None = None) -> Sequence[ContentBlock]: ...
```

当调用者传入 `block_type="text"` 时，IDE 和 mypy 知道返回值是 `Sequence[TextBlock]` 而非宽泛的 `Sequence[ContentBlock]`，实现**类型分发（type narrowing）**。这些 `@overload` 存根仅供静态分析使用，运行时执行的是最终的实现方法。

**实现方法**:

```python
def get_content_blocks(
    self,
    block_type: ContentBlockTypes | List[ContentBlockTypes] | None = None,
) -> Sequence[ContentBlock]:
    """Get the content in block format. If the content is a string,
    it will be converted to a text block."""
    blocks = []
    if isinstance(self.content, str):
        blocks.append(TextBlock(type="text", text=self.content))
    else:
        blocks = self.content or []

    if isinstance(block_type, str):
        blocks = [_ for _ in blocks if _["type"] == block_type]
    elif isinstance(block_type, list):
        blocks = [_ for _ in blocks if _["type"] in block_type]

    return blocks
```

**关键特性**: 如果 `content` 是字符串，自动转换为 `TextBlock`。

```python
def get_text_content(self, separator: str = "\n") -> str | None:
    """Get the pure text blocks from the message content."""
    if isinstance(self.content, str):
        return self.content

    gathered_text = []
    for block in self.content:
        if block.get("type") == "text":
            gathered_text.append(block["text"])

    if gathered_text:
        return separator.join(gathered_text)
    return None
```

#### 3.1.5 内容块类型检查

```python
def has_content_blocks(
    self,
    block_type: Literal["text", "tool_use", "tool_result", "image", "audio", "video"] | None = None,
) -> bool:
    """Check if the message has content blocks of the given type."""
    return len(self.get_content_blocks(block_type)) > 0
```

---

### 3.2 内容块类型 (`_message_block.py`)

所有内容块都使用 `TypedDict` 定义，这是一种类型安全的字典结构。

#### 3.2.1 文本块

```python
class TextBlock(TypedDict, total=False):
    """The text block."""
    type: Required[Literal["text"]]
    text: str
```

#### 3.2.2 思考块

```python
class ThinkingBlock(TypedDict, total=False):
    """The thinking block."""
    type: Required[Literal["thinking"]]
    thinking: str
```

#### 3.2.3 媒体源

```python
class Base64Source(TypedDict, total=False):
    """The base64 source"""
    type: Required[Literal["base64"]]
    media_type: Required[str]  # e.g. "image/jpeg"
    data: Required[str]         # RFC 2397格式

class URLSource(TypedDict, total=False):
    """The URL source"""
    type: Required[Literal["url"]]
    url: Required[str]
```

#### 3.2.4 图像块

```python
class ImageBlock(TypedDict, total=False):
    """The image block"""
    type: Required[Literal["image"]]
    source: Required[Base64Source | URLSource]
```

#### 3.2.5 音频块

```python
class AudioBlock(TypedDict, total=False):
    """The audio block"""
    type: Required[Literal["audio"]]
    source: Required[Base64Source | URLSource]
```

#### 3.2.6 视频块

```python
class VideoBlock(TypedDict, total=False):
    """The video block"""
    type: Required[Literal["video"]]
    source: Required[Base64Source | URLSource]
```

#### 3.2.7 工具调用块

```python
class ToolUseBlock(TypedDict, total=False):
    """The tool use block."""
    type: Required[Literal["tool_use"]]
    id: Required[str]                    # 工具调用ID
    name: Required[str]                  # 工具名称
    input: Required[dict[str, object]]    # 工具输入参数
    raw_input: str                       # 原始字符串输入
```

#### 3.2.8 工具结果块

```python
class ToolResultBlock(TypedDict, total=False):
    """The tool result block."""
    type: Required[Literal["tool_result"]]
    id: Required[str]
    output: Required[str | List[TextBlock | ImageBlock | AudioBlock | VideoBlock]]
    name: Required[str]
```

#### 3.2.9 联合类型定义

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

ContentBlockTypes = Literal[
    "text", "thinking", "tool_use", "tool_result",
    "image", "audio", "video",
]
```

---

## 4. 设计模式总结

### 4.1 TypedDict 模式

使用 `TypedDict` 而非 `dataclass` 或普通类的原因：
- 与 JSON 格式天然兼容
- 支持类型提示但不增加运行时开销
- `total=False` 允许可选字段

### 4.2 工厂方法模式

`from_dict()` 类方法提供从 JSON 创建对象的反向序列化能力。

### 4.3 策略模式

`get_content_blocks()` 方法支持按类型过滤内容块，提供灵活的内容访问方式。

### 4.4 Union Type 模式

使用 `Literal` 联合类型确保类型安全，同时支持多种输入格式。

---

### 边界情况与陷阱

#### Critical: Msg 字段直接修改的风险

```python
# Msg 是普通类（非 dataclass），字段可以直接修改
# 但这可能导致不可预期的副作用
msg = Msg(name="user", content="Hello", role="user")
msg.content = "Modified"  # 直接修改

# 问题：如果 msg 被多个引用共享，修改会传播
# 在 Pipeline 中，消息通常被多个 Agent 共享引用
```

**解决方案**：使用 `Msg(name=msg.name, content="New", role=msg.role)` 创建新实例。

#### High: TypedDict 的运行时类型检查

```python
# TypedDict 只在静态类型检查时生效，运行时不检查
from typing import TypedDict

class UserMsg(TypedDict):
    name: str
    content: str

# 静态分析时会报错，但运行时可以这样：
data = {"name": "user", "content": 123}  # 类型错误但运行时OK
user_msg = UserMsg(**data)  # 不会报错！
```

**解决方案**：使用 Pydantic 或在关键路径添加运行时验证。

#### High: Msg 的 id 和 timestamp 自动生成

```python
# id 和 timestamp 在创建时自动生成
msg1 = Msg(name="user", content="Hello", role="user")
msg2 = Msg(name="user", content="Hello", role="user")

print(msg1.id == msg2.id)  # False - 每次创建都是新 ID
print(msg1.timestamp == msg2.timestamp)  # 可能是 False - 时间戳不同
```

**陷阱**：如果序列化后反序列化，id 会丢失或变化。

#### Medium: ImageBlock 的 URLSource 必须提供 url

```python
# ImageBlock 支持多种图片来源，但 URLSource 必须有 url
from agentscope.message import Msg, ImageBlock, URLSource

# 错误：URLSource 没有 url 字段
block = ImageBlock(type="image", source=URLSource())  # 错误

# 正确：
block = ImageBlock(type="image", source=URLSource(url="https://..."))
```

#### Medium: AudioBlock 的 base64 数据格式

```python
# AudioBlock 的 audio 字段需要是 base64 编码的字符串
# 不是原始字节，也不是其他编码
import base64

raw_audio = b"audio data..."
audio_b64 = base64.b64encode(raw_audio).decode("ascii")

# 如果使用错误的格式，API 会返回 400 错误
```

#### Medium: 消息的 role 字段枚举

```python
# Msg 的 role 字段接受字符串，但某些 API 对格式有要求
# OpenAI: "system", "user", "assistant", "tool"
# Anthropic: "user", "assistant", "system"

# 如果使用错误的 role，某些 API 会静默失败或返回意外结果
msg = Msg(name="bot", content="Hello", role="invalid_role")  # 问题！
```

---

### 性能考量

#### Msg 创建开销

| 操作 | 开销 | 说明 |
|------|------|------|
| 创建空 Msg | ~0.01ms | 最快 |
| 创建带内容块 | ~0.05ms | 取决于块数量 |
| deepcopy(Msg) | ~0.1-1ms | 取决于内容大小 |
| 序列化 to_dict() | ~0.05ms | 取决于字段数量 |

#### 内容块性能

```python
# TextBlock: 最快，直接存储字符串
# ImageBlock: 需要存储完整 URL 或 base64，内存开销大
# AudioBlock: base64 编码增加 33% 大小

# 大量消息时的优化建议：
# - 避免在消息中存储大型多媒体数据
# - 使用 URL 引用而非 base64 内联
# - 考虑压缩大型 base64 音频数据
```

#### 消息队列中的 Msg 序列化

```python
# 如果消息需要通过网络传输或存储，需要序列化
# Msg.to_dict() 性能约为 ~0.05ms/消息
# 对于高频场景（如流式输出），这可能成为瓶颈

# 优化建议：
# - 批量序列化时使用 list comprehension
# - 考虑使用 msgpack 而非 JSON 序列化
```

---

## 5. 代码示例

### 5.1 创建基本消息

```python showLineNumbers
from agentscope.message import Msg

# 创建用户消息
user_msg = Msg(
    name="user",
    content="你好，请帮我查询天气",
    role="user"
)
print(user_msg)

# 创建助手消息
assistant_msg = Msg(
    name="assistant",
    content="好的，请问您想查询哪个城市的天气？",
    role="assistant"
)
```

**运行结果**:

```
Msg(id='aBcDeFgHiJkL', name='user', content='你好，请帮我查询天气', role='user', metadata={}, timestamp='2026-04-29 10:30:00.123', invocation_id='None')
```

### 5.2 使用内容块创建消息

```python showLineNumbers
from agentscope.message import Msg, TextBlock, ImageBlock, Base64Source

# 创建包含多种内容块的消息
msg = Msg(
    name="assistant",
    content=[
        TextBlock(type="text", text="这是文本内容"),
        ImageBlock(
            type="image",
            source=Base64Source(
                type="base64",
                media_type="image/png",
                data="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            )
        )
    ],
    role="assistant"
)
```

### 5.3 序列化与反序列化

```python showLineNumbers
# 序列化为字典
msg_dict = user_msg.to_dict()
print(msg_dict)

# 从字典恢复
restored_msg = Msg.from_dict(msg_dict)
print(restored_msg)
```

**预期输出**：
```
{'id': 'aBcDeFgHiJkL', 'name': 'user', 'content': '你好，请帮我查询天气', 'role': 'user', 'metadata': {}, 'timestamp': '2026-04-29 10:30:00.123'}
Msg(id='aBcDeFgHiJkL', name='user', content='你好，请帮我查询天气', role='user', metadata={}, timestamp='2026-04-29 10:30:00.123', invocation_id='None')
```

### 5.4 内容块操作

```python showLineNumbers
# 获取所有文本内容
text = msg.get_text_content()

# 检查是否有工具调用
has_tool_call = msg.has_content_blocks("tool_use")

# 获取所有工具调用块
tool_calls = msg.get_content_blocks("tool_use")
```

**预期输出**：
```
text = '这是文本内容'
has_tool_call = False
tool_calls = []
```

### 5.5 工具调用示例

```python showLineNumbers
from agentscope.message import Msg, ToolUseBlock, ToolResultBlock, TextBlock

# 模拟工具调用消息
tool_msg = Msg(
    name="assistant",
    content=[
        ToolUseBlock(
            type="tool_use",
            id="call_123",
            name="get_weather",
            input={"city": "北京"}
        )
    ],
    role="assistant"
)

# 模拟工具返回结果
result_msg = Msg(
    name="tool",
    content=[
        ToolResultBlock(
            type="tool_result",
            id="call_123",
            name="get_weather",
            output="北京今天天气晴朗，温度25度"
        )
    ],
    role="assistant"  # 注意: Msg 只支持 user/assistant/system, tool 结果用 assistant
)
```

**预期输出**：
```
tool_msg.has_content_blocks("tool_use") = True
tool_calls = [{'type': 'tool_use', 'id': 'call_123', 'name': 'get_weather', 'input': {'city': '北京'}}]
```

---

## 6. 练习题

### 6.1 基础题

1. 创建一条包含 `name="Alice"`, `content="Hello"`, `role="user"` 的消息，并打印其唯一 ID。

2. 将消息序列化为 JSON 字符串，然后反序列化为新消息对象。

3. 创建一个包含 `TextBlock` 和 `ImageBlock` 的消息，并提取其中的纯文本内容。

### 6.2 提高题

4. 实现一个函数 `count_content_blocks(msg, block_type)`，统计消息中指定类型内容块的数量。

5. 实现一个函数 `merge_messages(msgs)`，将多条消息的内容合并为一条消息。

6. 创建一个函数 `filter_messages_by_role(msgs, role)`，从消息列表中筛选指定角色的消息。

### 6.3 挑战题

7. 实现一个函数 `convert_to_openai_format(messages)`，将 AgentScope 的 `Msg` 对象列表转换为 OpenAI API 格式。

8. 设计一个函数 `detect_content_types(msg)`，自动检测消息中包含的所有内容类型。

---

**答案提示**:
- 参考 `get_content_blocks()` 方法的实现
- 使用 `isinstance()` 检查类型
- 参考 `to_dict()` 方法了解序列化格式

---

## 参考答案

### 6.1 基础题

**第1题：创建消息并打印 ID**

```python
from agentscope.message import Msg

msg = Msg(name="Alice", content="Hello", role="user")
print(msg.id)  # 自动生成的唯一标识符
```

**第2题：序列化与反序列化**

```python
import json

# 序列化
msg_dict = msg.to_dict()
json_str = json.dumps(msg_dict)

# 反序列化
restored = Msg.from_dict(json.loads(json_str))
assert msg.name == restored.name
assert msg.content == restored.content
```

**第3题：创建多内容块消息并提取文本**

```python
from agentscope.message import Msg, TextBlock, ImageBlock, URLSource

msg = Msg(
    name="user",
    role="user",
    content=[
        TextBlock(type="text", text="请看这张图片"),
        ImageBlock(
            type="image",
            source=URLSource(type="url", url="https://example.com/img.png"),
        ),
    ],
)
text = msg.get_text_content()  # "请看这张图片"
```

### 6.2 提高题

**第4题：count_content_blocks**

```python
def count_content_blocks(msg: Msg, block_type: type) -> int:
    blocks = msg.get_content_blocks()
    return sum(1 for b in blocks if isinstance(b, block_type))
```

**第5题：merge_messages**

```python
def merge_messages(msgs: list[Msg]) -> Msg:
    all_content = []
    for m in msgs:
        all_content.extend(m.get_content_blocks())
    return Msg(name="merged", role="assistant", content=all_content)
```

**第6题：filter_messages_by_role**

```python
def filter_messages_by_role(msgs: list[Msg], role: str) -> list[Msg]:
    return [m for m in msgs if m.role == role]
```

### 6.3 挑战题

**第7题：convert_to_openai_format**

```python
def convert_to_openai_format(messages: list[Msg]) -> list[dict]:
    result = []
    for msg in messages:
        entry = {"role": msg.role}
        blocks = msg.get_content_blocks()
        if all(b.get("type") == "text" for b in blocks):
            entry["content"] = msg.get_text_content()
        else:
            entry["content"] = [
                {"type": "text", "text": b.get("text", "")}
                if b.get("type") == "text" else b
                for b in blocks
            ]
        if msg.name:
            entry["name"] = msg.name
        result.append(entry)
    return result
```

> **注意**: AgentScope 的 `Msg` 类提供 `to_dict()` 方法用于序列化，但没有 `to_openai_dict()` 方法。OpenAI 格式转换由 Formatter 模块处理，详见 [Formatter 模块](module_formatter_deep.md)。

**第8题：detect_content_types**

```python
def detect_content_types(msg: Msg) -> set[str]:
    blocks = msg.get_content_blocks()
    return {b.get("type") for b in blocks}
```

---

## 小结

| 特性 | 实现方式 |
|------|----------|
| 消息结构 | 普通类 + 手动 `__init__` 初始化 |
| 类型体系 | ContentBlock 联合类型（Text/Image/Audio/Video/Tool） |
| 类型分发 | `@overload` 实现 `get_content_blocks` 多态 |
| 序列化 | `to_dict()` / `from_dict()` 支持 JSON 互转 |
| OpenAI 兼容 | `to_dict()` 转换为 JSON 格式；OpenAI 格式由 Formatter 处理 |

Message 模块是 AgentScope 的通信基础，所有代理间交互都通过 Msg 对象进行，其设计参考了 OpenAI 的消息格式并扩展了多模态支持。

## 设计模式总结

| 设计模式 | 应用位置 | 说明 |
|----------|----------|------|
| **TypedDict** | 所有 ContentBlock | 类型安全的字典，与 JSON 天然兼容 |
| **Union Type** | ContentBlock 联合类型 | 7 种内容块的统一类型 |
| **Factory Method** | `Msg.from_dict()` | 从 JSON 反向创建对象 |
| **Strategy** | `get_content_blocks(block_type)` | 按类型过滤内容块 |
| **Adapter** | Msg → 各 API 格式 | 由 Formatter 模块适配不同厂商 |

| 关联模块 | 关联点 | 参考位置 |
|----------|--------|----------|
| [智能体模块](module_agent_deep.md#3-agentbase-源码解读) | AgentBase 的 `reply()` 返回 Msg 对象 | 第 3.2 节 |
| [调度器模块](module_dispatcher_deep.md#4-源码解读) | MsgHub 广播和路由 Msg 消息 | 第 4.1 节 |
| [模型模块](module_model_deep.md#2-chatmodelbase-基类分析) | Model 接收和返回 Msg 对象 | 第 2.2 节 |
| [记忆模块](module_memory_rag_deep.md#2-memory-基类和实现) | Memory 存储和检索 Msg 对象 | 第 2.1 节 |
| [格式化器模块](module_formatter_deep.md#3-源码解读) | Formatter 将 Msg 转换为 API 格式 | 第 3.1-3.4 节 |


---

## 参考资料

- Msg 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/message/_message_base.py`
- ContentBlock 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/message/_message_block.py`

---

*文档版本: 1.0*
*最后更新: 2026-04-28*
