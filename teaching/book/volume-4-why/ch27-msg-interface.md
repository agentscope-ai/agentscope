# 第二十七章 消息为什么是唯一接口

在 AgentScope 的世界里，所有组件之间的对话只用一种语言：`Msg`。Agent 接收 `Msg`、返回 `Msg`；Memory 存储 `Msg`、返回 `list[Msg]`；Tool 的调用请求和结果嵌入 `Msg` 的 ContentBlock；Formatter 接收 `list[Msg]` 再转换为 API 格式；连 MsgHub 的广播也以 `Msg` 为单位。这不是偶然，而是一个刻意的设计决策。

## 一、决策回顾

让我们从源码中逐层追踪 `Msg` 的流动路径。

### 1.1 Msg 的结构

`Msg` 类定义在 `src/agentscope/message/_message_base.py` 第 21 行。它的结构极其简洁：

```python
class Msg:
    def __init__(
        self,
        name: str,
        content: str | Sequence[ContentBlock],
        role: Literal["user", "assistant", "system"],
        metadata: dict[str, JSONSerializableObject] | None = None,
        timestamp: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
```

六个字段，没有继承层次，没有泛型参数。`content` 可以是纯字符串，也可以是一系列 `ContentBlock`（文本、工具调用、工具结果、图像、音频、视频、思考过程）。这种 "字符串或结构化块列表" 的设计（第 28 行的 `content: str | Sequence[ContentBlock]`）是一个关键的折中：简单场景用字符串就够了，复杂场景可以组装任意类型的内容块。

`ContentBlock` 定义在 `src/agentscope/message/_message_block.py` 第 110-118 行，是一个联合类型：

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

七种内容块，全部使用 TypedDict 定义。它们不是 `Msg` 的子类，而是嵌入 `Msg.content` 字段中的数据结构。这意味着 `Msg` 本身不关心它携带的是什么类型的内容——它只负责传输。

### 1.2 Agent 与 Msg

`AgentBase` 的核心方法签名在 `src/agentscope/agent/_agent_base.py`：

- `reply` 方法（第 197 行）：`async def reply(self, *args, **kwargs) -> Msg`
- `observe` 方法（第 185 行）：`async def observe(self, msg: Msg | list[Msg] | None) -> None`
- `__call__` 方法（第 448 行）：`async def __call__(self, *args, **kwargs) -> Msg`
- `print` 方法（第 205 行）：`async def print(self, msg: Msg, ...) -> None`
- `_broadcast_to_subscribers` 方法（第 469 行）：参数和内部调用全部是 `Msg`

注意一个不对称设计：`reply` 的签名是 `*args, **kwargs`，不强制要求 `Msg` 输入；但返回值类型严格是 `Msg`。而 `observe` 则明确要求 `Msg` 作为输入。理由是某些 Agent 的 reply 不需要外部消息触发（比如主动发起对话），但所有 Agent 观察到的都必须是结构化消息。

广播机制（第 469-485 行）清晰地展示了 Agent 间的通信链路：

```python
async def _broadcast_to_subscribers(self, msg: Msg | list[Msg] | None) -> None:
    broadcast_msg = self._strip_thinking_blocks(msg)
    for subscribers in self._subscribers.values():
        for subscriber in subscribers:
            await subscriber.observe(broadcast_msg)
```

链路是：`reply` 产生 `Msg` -> `__call__` 捕获（第 463 行）-> `_broadcast_to_subscribers` 分发 -> 其他 Agent 的 `observe` 接收。整条链路上流动的只有 `Msg`。注意第 481 行的 `_strip_thinking_blocks`——广播前会剥离 thinking 块，因为推理过程属于 Agent 的内部状态，不应暴露给其他 Agent。

### 1.3 Model 与 Msg

`ChatModelBase` 定义在 `src/agentscope/model/_model_base.py`，它的 `__call__` 方法（第 39 行）返回的是 `ChatResponse`，不是 `Msg`：

```python
async def __call__(self, *args, **kwargs) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
```

`ChatResponse` 定义在 `src/agentscope/model/_model_response.py` 第 20 行：

```python
@dataclass
class ChatResponse:
    content: Sequence[TextBlock | ToolUseBlock | ThinkingBlock | AudioBlock]
    id: str
    created_at: str
    type: Literal["chat"]
    usage: ChatUsage | None
    metadata: dict[str, JSONSerializableObject] | None
```

这里有一个关键的转换边界：Model 层使用自己的 `ChatResponse` 类型，而非直接返回 `Msg`。转换发生在 `ReActAgent` 的 `_reasoning` 方法中。在 `src/agentscope/agent/_react_agent.py` 第 584 行：

```python
msg = Msg(name=self.name, content=[], role="assistant")
if self.model.stream:
    async for content_chunk in res:
        msg.invocation_id = content_chunk.id
        msg.content = content_chunk.content
else:
    msg.invocation_id = res.id
    msg.content = list(res.content)
```

Agent 从 `ChatResponse` 中提取 `content` 和 `id`，包装成 `Msg`。这个转换是单向的：`ChatResponse` -> `Msg`。Model 不需要知道 `Msg` 的存在。

### 1.4 Tool 与 Msg

Tool 系统的返回类型是 `ToolResponse`（定义在 `src/agentscope/tool/_response.py` 第 12 行），也不是 `Msg`：

```python
@dataclass
class ToolResponse:
    content: List[TextBlock | ImageBlock | AudioBlock | VideoBlock]
    metadata: Optional[dict] = None
    stream: bool = False
    is_last: bool = True
    is_interrupted: bool = False
```

转换同样发生在 Agent 层。`ReActAgent._acting` 方法（第 657-714 行）将 `ToolResponse` 包装成包含 `ToolResultBlock` 的 `Msg`：

```python
tool_res_msg = Msg(
    "system",
    [ToolResultBlock(
        type="tool_result",
        id=tool_call["id"],
        name=tool_call["name"],
        output=[],
    )],
    "system",
)
async for chunk in tool_res:
    tool_res_msg.content[0]["output"] = chunk.content
```

第 671-692 行：Agent 创建一个系统 `Msg`，将 `ToolResponse` 的内容逐步填入 `ToolResultBlock` 的 `output` 字段，最后存入 Memory。

### 1.5 Memory 与 Msg

`MemoryBase` 定义在 `src/agentscope/memory/_working_memory/_base.py`。它的所有核心接口都以 `Msg` 为单位：

- `add` 方法（第 32 行）：`async def add(self, memories: Msg | list[Msg] | None, ...) -> None`
- `get_memory` 方法（第 105 行）：`async def get_memory(self, ...) -> list[Msg]`

Memory 不关心 `Msg` 内部是文本还是工具调用——它只负责存储和检索。`InMemoryMemory`、`RedisMemory`、`SQLAlchemyMemory` 都只需要实现 `Msg` 的存取，而 `Msg` 的 `to_dict` / `from_dict` 方法（第 75-99 行）提供了统一的序列化接口。

### 1.6 Formatter 与 Msg

`FormatterBase` 定义在 `src/agentscope/formatter/_formatter_base.py`，它的 `format` 方法接收 `list[Msg]`，输出 API 格式的字典列表。第 20 行的 `assert_list_of_msgs` 方法验证输入必须是 `Msg` 对象。每个 Formatter 实现（OpenAI、Anthropic、DashScope、Gemini 等）都从 `Msg` 的 `role`、`content`、`name` 字段构建对应 API 的消息格式。

至此，`Msg` 的流转路径完整了：用户输入 -> `Msg` -> Memory -> Formatter -> Model -> `ChatResponse` -> Agent 转换为 `Msg` -> Memory -> Formatter -> Model -> ... 如此循环。Tool 的结果通过 `ToolResponse` -> Agent 转换为 `Msg` -> 重新进入循环。

## 二、被否方案

更直觉的设计可能是分类型消息。比如：

```python
class UserMessage:
    text: str
    images: list[Image]

class AssistantMessage:
    text: str
    tool_calls: list[ToolCall]

class ToolResultMessage:
    tool_call_id: str
    result: Any

class SystemMessage:
    instruction: str
```

这种方式的好处是类型精确：编译器能帮你检查 Agent 不应发送 `ToolResultMessage`，Memory 不需要存储 `SystemMessage`。每个组件只处理自己关心的消息类型。如果你在 Agent 的 `reply` 方法里返回了 `ToolResultMessage`，类型检查器会立刻报错。

另一种方案是使用 Pydantic `BaseModel` 提供更强的类型验证：

```python
from pydantic import BaseModel

class BaseMessage(BaseModel):
    name: str
    role: str
    content: str

class ChatMessage(BaseMessage):
    pass

class ToolCallMessage(BaseMessage):
    tool_calls: list[ToolCall]
```

Pydantic 天然支持 JSON schema 生成、字段验证、不可变性。看起来比 TypedDict 更可靠。

还有一种更激进的方案——完全取消消息类，所有组件直接传递字典：

```python
# 没有 Msg 类，直接用字典
agent.reply({"role": "user", "content": "hello"})
model.call([{"role": "system", "content": "..."}, ...])
memory.add({"role": "assistant", "content": "result", "tool_calls": [...]})
```

这在灵活性上无与伦比，但放弃了所有结构化保证。

## 三、后果分析

### 3.1 好处：统一的序列化接口

统一接口极大地降低了序列化和持久化的复杂度。`Msg` 只需要一对 `to_dict` / `from_dict`（第 75-99 行），所有 Memory 实现都可以直接存储和恢复。看 `InMemoryMemory`、`RedisMemory`、`SQLAlchemyMemory`——它们都只需要处理一种类型的序列化。

如果每种消息类型都有独立的序列化逻辑，每种 Memory 实现都需要为每种消息类型写适配代码。假设有 4 种消息类型和 4 种 Memory 实现，那就是 16 组序列化逻辑，而非现在的 4 组。

### 3.2 好处：广播机制的简洁性

MsgHub 的广播机制因为消息格式统一而极其简洁。看第 469 行的 `_broadcast_to_subscribers`：收到消息，剥离 thinking 块，逐个调用 `observe`。如果消息类型多样，广播逻辑需要根据消息类型做分发：

```python
# 假设使用多类型消息，广播逻辑会变成：
async def _broadcast(self, msg: BaseMessage):
    if isinstance(msg, AssistantMessage):
        for sub in self._subscribers.values():
            await sub.observe_assistant(msg)
    elif isinstance(msg, ToolResultMessage):
        for sub in self._subscribers.values():
            await sub.observe_tool_result(msg)
    # ...
```

每一对 Agent 间的通信都需要匹配消息类型，组合爆炸不可避免。

### 3.3 好处：content 多态的精妙折中

`content: str | Sequence[ContentBlock]` 的设计（第 28 行）让 `Msg` 在简单场景和复杂场景之间自由切换。纯文本对话用字符串，工具调用用 `ToolUseBlock` 列表，多模态场景混合使用各种 Block。调用方不需要关心内部结构：

- `get_text_content()` 方法（第 123 行）自动提取纯文本，无论 content 是字符串还是块列表
- `get_content_blocks()` 方法（第 198 行）按类型过滤，支持 overload 类型推导
- `has_content_blocks()` 方法（第 101 行）快速判断是否包含特定类型

在 `ReActAgent` 的推理循环中（第 442-444 行），这些方法让代码保持清晰：

```python
for tool_call in msg_reasoning.get_content_blocks("tool_use"):
    futures.append(self._acting(tool_call))
```

不需要 `isinstance` 检查，不需要类型转换。`Msg` 自身的查询方法封装了所有细节。

### 3.4 代价：类型安全的丧失

`Msg` 类本身没有类型区分。第 61 行的 `assert role in ["user", "assistant", "system"]` 是唯一的角色检查，但 `role` 只是三个字符串值。当你写 `msg.role == "assistant"` 时，没有编译时保证告诉你这个消息一定包含工具调用块。一个 role 为 "assistant" 的 `Msg` 可能包含纯文本、工具调用、或者两者都有——类型系统无法区分。

`metadata` 字段（第 64 行）是一个自由形式的字典。结构化输出、工具调用追踪等信息都以非类型安全的方式存储。当你需要从 `metadata` 中读取结构化数据时，面对的是 `dict[str, JSONSerializableObject]`，而不是一个有明确字段的对象。`ReActAgent` 第 706 行就是这么做的：

```python
if chunk.metadata and chunk.metadata.get("success", False):
    return chunk.metadata.get("structured_output")
```

这里的 `success` 和 `structured_output` 是字符串键，没有类型检查。拼错一个字符就是 bug。

### 3.5 代价：Msg 的语义过载

因为 `Msg` 要承载所有场景，它的语义范围被极大拉伸。一个 `Msg(name="system", content=[ToolResultBlock(...)], role="system")` 和一个 `Msg(name="user", content="hello", role="user")` 的用途完全不同，但它们的类型签名一模一样。阅读代码时，你必须从 `name`、`role` 和 `content` 的组合推断一条 `Msg` 的真实含义，而非直接从类型名获取。

`ReActAgent._acting` 方法（第 671 行）创建的系统消息就是一个例子：

```python
tool_res_msg = Msg("system", [ToolResultBlock(...)], "system")
```

这条消息代表"工具执行结果"，但它的类型是 `Msg`，和用户输入、Agent 回复、系统提示共享同一个类型。区分全靠 `role="system"` 和 `content` 中的 `ToolResultBlock`。

## 四、横向对比

**LangChain** 选择了另一条路。它定义了 `BaseMessage` 基类和多个子类：`HumanMessage`、`AIMessage`、`SystemMessage`、`ToolMessage`、`FunctionMessage`。每个子类有特定的字段：`AIMessage` 有 `tool_calls`，`ToolMessage` 有 `tool_call_id`。类型精确，但类层次复杂。LangChain 还需要维护一个消息转换层，把这些内部类型映射到各种 LLM API 的消息格式。当 API 增加新的消息类型时（比如 Anthropic 的 thinking block），LangChain 需要在类层次中添加新类型或扩展现有类型。

**AutoGen** 的方式更接近 AgentScope。它的 `BaseMessage` 也是统一的消息类，包含 `role`、`content`、`name` 等字段。但 AutoGen 在消息之上叠加了一层 `ChatResult` 和 `ChatMessage` 的封装，增加了间接层。AutoGen 的消息还可以携带 `context` 字典用于传递额外信息，这与 AgentScope 的 `metadata` 异曲同工。两者的设计哲学相似：用单一消息类型 + 自由格式扩展字段来保持灵活性。

**CrewAI** 使用了最简方案：消息基本上就是字典或字符串，没有专门的消息类。它的 Agent 接口返回的是自由形式的文本结果，类型约束最弱。这在简单场景下开发速度快，但在复杂流水线中缺乏结构化保证。CrewAI 的选择暗示它定位于简单的工作流编排，而非需要精确消息追踪的复杂 Agent 系统。

AgentScope 的选择处于 LangChain 的多类型体系和 CrewAI 的无类型体系之间。`Msg` 是唯一的消息类型，但通过 `content` 的多态结构（字符串 + 七种 ContentBlock）来容纳各种场景。值得注意的是，AgentScope 并非让 `Msg` 承担所有职责——Model 层使用 `ChatResponse`，Tool 层使用 `ToolResponse`，只在 Agent 边界进行转换。这种"内部自由、边界统一"的策略是实际的折中：各模块内部可以使用最自然的类型，但模块间的通信严格通过 `Msg`。

## 五、你的判断

如果你在设计一个多 Agent 框架，你会选择哪种消息设计？

A. 单一 `Msg` 类 + 多态 `content`（AgentScope 的方案）
B. 多类型消息层次（LangChain 的方案）
C. 无类型字典/字符串（CrewAI 的方案）
D. Pydantic `BaseModel` 基类 + 泛型约束

在选择之前，想清楚你的框架最频繁的操作是什么。如果 Agent 间的广播和存储是核心路径，统一类型能极大简化代码——AgentScope 的 `_broadcast_to_subscribers` 只有 10 行，正因如此。如果类型安全和编译时检查更重要，多类型层次更合适——当你重构消息结构时，类型检查器能帮你找到所有受影响的代码。如果框架的目标用户是快速原型开发者，无类型方案的门槛最低。

还有一层考虑被忽略：`Msg` 的统一性使得 Agent 可以无条件地转发消息。在 MsgHub 模式下，一个 Agent 的输出直接成为另一个 Agent 的输入，不需要任何类型检查或转换。如果使用多类型消息，这种链式传递需要每一环都理解前后的消息类型约定，增加了耦合。

AgentScope 的选择暗示了它的核心假设：消息在 Agent 之间频繁流动和持久化，统一的序列化接口和广播简洁性比类型精确性更重要。

但反过来问：当 `metadata` 字段里的隐式约定越来越多——`success`、`structured_output`、`_is_interrupted`、`rewritten_query`——这种"灵活的自由字典"是否正在演变成一个无法追踪的暗面协议？你如何在保持 `Msg` 统一性的同时，防止 `metadata` 变成一个无所不包的黑洞？
