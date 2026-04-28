# Message 消息系统源码深度剖析

## 目录
1. 模块概述
2. 目录结构
3. 核心类与函数源码解读
4. 设计模式总结
5. 代码示例
6. 练习题

---

## 1. 模块概述

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

## 5. 代码示例

### 5.1 创建基本消息

```python
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

### 5.2 使用内容块创建消息

```python
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

```python
# 序列化为字典
msg_dict = user_msg.to_dict()
print(msg_dict)

# 从字典恢复
restored_msg = Msg.from_dict(msg_dict)
print(restored_msg)
```

### 5.4 内容块操作

```python
# 获取所有文本内容
text = msg.get_text_content()

# 检查是否有工具调用
has_tool_call = msg.has_content_blocks("tool_use")

# 获取所有工具调用块
tool_calls = msg.get_content_blocks("tool_use")
```

### 5.5 工具调用示例

```python
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
