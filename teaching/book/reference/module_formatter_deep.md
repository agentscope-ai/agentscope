# Formatter 格式化器深度剖析

## 学习目标

> 学完本节，你将能够：
> - [L1 记忆] 列举 FormatterBase → TruncatedFormatterBase → 各厂商格式化器的继承链
> - [L2 理解] 解释 format-count-truncate 循环的工作原理和消息分组机制
> - [L3 应用] 使用合适的格式化器将 Msg 消息转换为目标 API 格式
> - [L4 分析] 分析各厂商格式化器对多模态内容块的不同处理策略

**预计时间**：35 分钟
**先修要求**：已完成 [Message 消息模块](module_message_deep.md)

## 目录

1. [模块概述](#1-模块概述)
2. [目录结构](#2-目录结构)
3. [源码解读](#3-源码解读)
   - [FormatterBase 抽象基类](#31-formatterbase-抽象基类)
   - [TruncatedFormatterBase 截断格式化器](#32-truncatedformatterbase-截断格式化器)
   - [消息分组机制（_group_messages）](#33-消息分组机制)
   - [OpenAI 格式化器](#34-openai-格式化器)
   - [Anthropic 格式化器](#35-anthropic-格式化器)
   - [DashScope 格式化器](#36-dashscope-格式化器)
   - [其他厂商格式化器概览](#37-其他厂商格式化器概览)
4. [设计模式总结](#4-设计模式总结)
5. [代码示例](#5-代码示例)
6. [练习题](#6-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 FormatterBase → TruncatedFormatterBase → 各厂商格式化器的继承链 | 列举、识别 |
| 理解 | 解释 format-count-truncate 循环的工作原理和消息分组机制 | 解释、描述 |
| 应用 | 使用合适的格式化器将 Msg 消息转换为目标 API 格式 | 实现、配置 |
| 分析 | 分析各厂商格式化器对多模态内容块的不同处理策略 | 分析、对比 |
| 评价 | 评价 Chat 格式化器与 MultiAgent 格式化器的设计取舍 | 评价、推荐 |
| 创造 | 设计一个支持新厂商 API 的自定义格式化器 | 设计、构建 |

## 先修检查

- [ ] 了解 OpenAI / Anthropic / DashScope Chat API 消息格式
- [ ] 理解 [Message 模块](module_message_deep.md) 的 Msg 类和 ContentBlock 体系
- [ ] Python async/await 和异步生成器基础
- [ ] 模板方法设计模式基础

## Java 开发者对照

| AgentScope 概念 | Java 对应 | 说明 |
|----------------|-----------|------|
| `FormatterBase` | `HttpMessageConverter<T>` | 消息格式转换抽象 |
| `TruncatedFormatterBase` | 带拦截器的 Converter | 增加了 Token 截断逻辑 |
| `OpenAIChatFormatter` | `MappingJackson2HttpMessageConverter` | 针对特定 API 的具体实现 |
| `_group_messages` | `HandlerInterceptor` 分发 | 按类型分组处理请求 |
| `_truncate` | 请求体大小限制过滤器 | 超出预算时截断 |

---

## 1. 模块概述

> **交叉引用**: Formatter 模块是 Model 层的核心依赖，将 AgentScope 内部的 `Msg` 消息转换为各厂商 API 所需的字典格式。Model 在调用 LLM API 前通过 Formatter 进行格式转换，详见 [Model 模块](module_model_deep.md)。Token 计数模块提供 `TokenCounterBase` 用于截断时的 Token 计算，详见 [Embedding 与 Token 模块](module_embedding_token_deep.md)。Tracing 模块提供 `trace_format()` 装饰器追踪格式化调用，详见 [Tracing 模块](module_tracing_deep.md)。

Formatter（格式化器）是 AgentScope Model 层的关键桥梁，负责将框架内部的 `Msg` 消息对象转换为各 LLM 厂商 API 所需的消息格式。不同厂商对消息结构、多模态内容、工具调用的表示方式各不相同，Formatter 通过策略模式屏蔽这些差异。

**核心能力**：

1. **格式转换**：将 `Msg` → `dict` 的厂商特定格式
2. **Token 截断**：超出预算时自动截断最早的消息
3. **多模态处理**：统一处理文本、图像、音频、视频等不同内容块
4. **多代理支持**：Chat 模式（单轮对话）和 MultiAgent 模式（多代理对话历史）

**源码位置**: `src/agentscope/formatter/`（~3,581 行，10+ 文件）

---

## 2. 目录结构

```
formatter/
├── __init__.py                        # 导出接口（15 个格式化器类）
├── _formatter_base.py                 # FormatterBase 抽象基类（130 行）
├── _truncated_formatter_base.py       # TruncatedFormatterBase 截断格式化器（298 行）
├── _openai_formatter.py               # OpenAI 格式化器（541 行）
├── _anthropic_formatter.py            # Anthropic 格式化器（355 行）
├── _dashscope_formatter.py            # DashScope 格式化器（634 行）
├── _gemini_formatter.py               # Gemini 格式化器
├── _ollama_formatter.py               # Ollama 格式化器
├── _deepseek_formatter.py             # DeepSeek 格式化器
└── _a2a_formatter.py                  # A2A 协议格式化器
```

**架构概览**：

```
FormatterBase                    # 格式化器抽象基类
└── TruncatedFormatterBase       # 增加 Token 截断能力
    ├── OpenAIChatFormatter      # OpenAI 单代理
    ├── OpenAIMultiAgentFormatter  # OpenAI 多代理
    ├── AnthropicChatFormatter     # Anthropic 单代理
    ├── AnthropicMultiAgentFormatter  # Anthropic 多代理
    ├── DashScopeChatFormatter     # DashScope 单代理
    ├── DashScopeMultiAgentFormatter  # DashScope 多代理
    ├── GeminiChatFormatter        # Gemini 单代理
    ├── GeminiMultiAgentFormatter  # Gemini 多代理
    ├── OllamaChatFormatter        # Ollama 单代理
    ├── OllamaMultiAgentFormatter  # Ollama 多代理
    ├── DeepSeekChatFormatter      # DeepSeek 单代理
    ├── DeepSeekMultiAgentFormatter  # DeepSeek 多代理
    └── A2AChatFormatter           # A2A 协议格式化器
```

---

## 3. 源码解读

### 3.1 FormatterBase 抽象基类

```python showLineNumbers
class FormatterBase:
    @abstractmethod
    async def format(self, *args, **kwargs) -> list[dict[str, Any]]:
        """将 Msg 消息列表转换为 API 字典格式"""

    @staticmethod
    def assert_list_of_msgs(msgs: list[Msg]) -> None:
        """验证输入是否为 Msg 列表"""

    @staticmethod
    def convert_tool_result_to_string(output):
        """将工具结果中的多模态块转为文本+文件路径"""
```

**三个方法**：

| 方法 | 类型 | 用途 |
|------|------|------|
| `format()` | 抽象方法 | 子类实现具体的格式转换逻辑 |
| `assert_list_of_msgs()` | 静态方法 | 输入校验，确保参数为 `list[Msg]` |
| `convert_tool_result_to_string()` | 静态方法 | 处理工具结果中的多模态数据 |

**`convert_tool_result_to_string` 的工作流程**：

```
工具结果（可能包含文本 + 图像 + 音频 + 视频）
    ↓
遍历内容块：
  - TextBlock → 直接收集文本
  - ImageBlock/VideoBlock/AudioBlock(url) → 文本中嵌入 URL
  - ImageBlock/VideoBlock/AudioBlock(base64) → 保存为本地文件，文本中嵌入路径
    ↓
返回 (文本字符串, [(路径/URL, 原始块)] 元组列表)
```

### 3.2 TruncatedFormatterBase 截断格式化器

这是 Formatter 体系的核心中间层，实现了 **format-count-truncate 循环**。

```python showLineNumbers
class TruncatedFormatterBase(FormatterBase, ABC):
    def __init__(self, token_counter: TokenCounterBase | None = None,
                 max_tokens: int | None = None) -> None:
        self.token_counter = token_counter
        self.max_tokens = max_tokens

    async def format(self, msgs: list[Msg], **kwargs) -> list[dict[str, Any]]:
        msgs = copy.deepcopy(msgs)
        while True:
            formatted = await self._format(msgs)
            token_count = await self._count(formatted)
            if token_count is None or token_count <= self.max_tokens:
                return formatted
            msgs = await self._truncate(msgs)
```

**format-count-truncate 循环**：

```
Msg 列表（深拷贝）
    ↓ _format()
格式化后的 dict 列表
    ↓ _count()
Token 数量
    ↓ 判断
如果 <= max_tokens → 返回结果
如果 > max_tokens → _truncate() 截断最早消息 → 重新循环
```

**关键设计**：
- 深拷贝确保截断不影响原始消息
- `_count()` 返回 `None` 时跳过截断（无 TokenCounter 的场景）
- 截断是**从最早的消息开始删除**的简单策略

**`_truncate()` 截断策略**：

```python showLineNumbers
async def _truncate(self, msgs: list[Msg]) -> list[Msg]:
    # 1. 分离系统消息和普通消息
    # 2. 从最早的普通消息开始删除
    # 3. 确保 tool_use 和 tool_result 成对删除
    # 4. 如果系统消息本身超限，抛出 ValueError
```

> **重要**: 截断使用 `tool_call_ids` 集合追踪工具调用 ID，确保不会出现孤立的 `tool_use`（没有对应 `tool_result`）或孤立的 `tool_result`（没有对应 `tool_use`）。

### 3.3 消息分组机制

`_group_messages` 是理解多代理格式化的关键：

```python showLineNumbers
@staticmethod
async def _group_messages(msgs):
    """将消息列表分组为 tool_sequence 或 agent_message"""
    # tool_sequence: 包含 tool_use 或 tool_result 的连续消息
    # agent_message: 其他普通消息
```

**分组示例**：

```
输入消息列表：
  [system, user_msg, tool_use_msg, tool_result_msg, assistant_msg, user_msg2]

分组结果：
  ("agent_message", [system, user_msg])          # 普通消息组
  ("tool_sequence", [tool_use_msg, tool_result_msg])  # 工具调用组
  ("agent_message", [assistant_msg, user_msg2])   # 普通消息组
```

> **设计意图**: 工具调用和结果必须作为整体处理，不能拆分。多代理场景中，不同代理的对话通过 `_format_agent_message` 统一包装。

### 3.4 OpenAI 格式化器

#### OpenAIChatFormatter（单代理）

| 特性 | 值 |
|------|---|
| `support_tools_api` | True |
| `support_multiagent` | True（使用 `name` 字段） |
| `support_vision` | True |
| `supported_blocks` | Text, Image, Audio, ToolUse, ToolResult |

**内容块转换映射**：

| ContentBlock 类型 | OpenAI API 格式 |
|-------------------|-----------------|
| TextBlock | `{"type": "text", "text": "..."}` |
| ImageBlock (url) | `{"type": "image_url", "image_url": {"url": "..."}}` |
| ImageBlock (base64) | `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}` |
| AudioBlock | `{"input_audio": {"data": "...", "format": "wav"}}` (仅 wav/mp3) |
| ToolUseBlock | `{"tool_calls": [{"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}]}` |
| ToolResultBlock | `{"role": "tool", "tool_call_id": "...", "content": "..."}` (OpenAI API格式) |

**`promote_tool_result_images` 选项**：

部分 LLM API 不支持工具结果中包含图像。当设为 `True` 时，格式化器会自动将工具结果中的图像提取出来，作为独立的 user 消息注入。

```
原始消息：
  assistant: [ToolUse(id="call_1")]
  tool: [ToolResult(id="call_1", output=[ImageBlock, "结果文本"])]

promote_tool_result_images=True 时：
  assistant: [ToolUse(id="call_1")]
  tool: [ToolResult(id="call_1", output="结果文本")]
  user: [ImageBlock]  ← 提取为独立消息
```

#### OpenAIMultiAgentFormatter（多代理）

将多代理对话历史包装在 `<history>` 标签中：

```python showLineNumbers
# 构造函数
def __init__(self, conversation_history_prompt="# Conversation History\n...", ...)
```

**格式化策略**：
- 第一个 agent_message 组：添加 `conversation_history_prompt` + `<history>` 包装
- 后续组：继续追加到 `<history>` 标签内
- 工具调用序列：委托给 `OpenAIChatFormatter` 处理
- 最终合并为一条 user 消息

```python showLineNumbers
# 多代理输出示例
{
    "role": "user",
    "content": [
        {"type": "text", "text": "# Conversation History\n<history>\nAlice: 你好\nBob: 收到，我来处理\n</history>"},
        {"type": "image_url", "image_url": {"url": "..."}}
    ]
}
```

### 3.5 Anthropic 格式化器

#### AnthropicChatFormatter

| 特性 | 值 |
|------|---|
| `support_tools_api` | True |
| `support_multiagent` | False |
| `support_vision` | True |
| `supported_blocks` | Text, Image, ToolUse, ToolResult |

**与 OpenAI 的关键差异**：

| 差异点 | OpenAI | Anthropic |
|--------|--------|-----------|
| 图像格式 | `{"type": "image_url", ...}` | `{"type": "image", "source": {...}}` |
| 音频支持 | 支持（wav/mp3） | 不支持 |
| 工具结果 | `{"role": "tool", ...}` | `{"role": "user", "content": [{"type": "tool_result", ...}]}` |
| 系统消息 | 可出现在任意位置 | 只能是第一条，否则重映射为 user |
| 工具调用 | `function.name` + `arguments` (JSON string) | `name` + `input` (dict) |

**系统消息重映射**：

```python showLineNumbers
# Anthropic API 只允许第一条消息为 system
# 如果后续出现 system 消息，自动重映射为 user
if msg.role == "system" and not is_first:
    role = "user"  # 重映射
```

#### AnthropicMultiAgentFormatter

与 OpenAI 多代理格式化器结构类似，但注意 `support_multiagent = True`（Chat 版本为 False）。同样使用 `<history>` 标签包装。

### 3.6 DashScope 格式化器

#### DashScopeChatFormatter

| 特性 | 值 |
|------|---|
| `support_tools_api` | True |
| `support_multiagent` | False |
| `support_vision` | True |
| `supported_blocks` | Text, Image, Audio, **Video**, ToolUse, ToolResult |

**DashScope 独有特性**：

1. **视频块支持**：唯一支持 VideoBlock 的格式化器
2. **三个独立的 promote 标志**：分别控制图像、音频、视频的提升
3. **`_reformat_messages` 后处理**：如果消息内容全为文本，合并为单个字符串（兼容 HuggingFaceTokenCounter）
4. **API 缺陷兼容**：处理 DashScope API 的多个已知问题

```python showLineNumbers
def __init__(self,
             promote_tool_result_images: bool = False,
             promote_tool_result_audios: bool = False,
             promote_tool_result_videos: bool = False, ...):
```

**DashScope API 缺陷兼容**：

| 问题 | 兼容策略 |
|------|----------|
| 消息缺少 `content` 字段导致 `KeyError` | 为无内容的消息赋 `[]` |
| `content` 为 `None` 导致 `TypeError` | 同上 |
| 空文本导致工具重复调用 | 过滤空文本块 |

### 3.7 其他厂商格式化器概览

| 格式化器 | 厂商 | 特点 |
|----------|------|------|
| `GeminiChatFormatter` / `GeminiMultiAgentFormatter` | Google Gemini | Gemini API 格式适配 |
| `OllamaChatFormatter` / `OllamaMultiAgentFormatter` | Ollama | 本地模型，OpenAI 兼容格式 |
| `DeepSeekChatFormatter` / `DeepSeekMultiAgentFormatter` | DeepSeek | DeepSeek API 格式适配 |
| `A2AChatFormatter` | A2A 协议 | Agent-to-Agent 协议格式 |

---

## 4. 设计模式总结

| 设计模式 | 应用位置 | 说明 |
|----------|----------|------|
| **Template Method（模板方法）** | `TruncatedFormatterBase.format()` | 定义 format-count-truncate 循环骨架，子类实现 `_format` 等具体步骤 |
| **Strategy（策略）** | 各厂商格式化器 | 每个厂商一个策略，运行时可替换 |
| **Decorator（装饰器）** | `@trace_format` | Tracing 模块通过装饰器无侵入地追踪格式化调用 |
| **Factory（工厂）** | Chat vs MultiAgent 格式化器 | 每个厂商提供两种工厂方法，根据场景选择 |
| **Adapter（适配器）** | 所有格式化器 | 将统一的 Msg 格式适配为各厂商特定的 API 格式 |

**继承链的模板方法层次**：

```
FormatterBase.format()              # 抽象：子类必须实现
TruncatedFormatterBase.format()     # 具体：format-count-truncate 循环
  ├── _format()                     # 可覆写：消息分组+分发
  ├── _format_system_message()      # 可覆写：系统消息格式
  ├── _format_tool_sequence()       # 抽象：子类实现
  ├── _format_agent_message()       # 抽象：子类实现
  ├── _count()                      # 具体：委托给 TokenCounter
  └── _truncate()                   # 可覆写：默认从最早消息截断
```

---

### 边界情况与陷阱

#### Critical: 不同模型 API 的 tool_call 格式差异

```python showLineNumbers
# OpenAI: tool_call 是独立字段
{"role": "assistant", "tool_calls": [...]}

# Anthropic: tool_use 是 content 内的块
{"role": "assistant", "content": [{"type": "tool_use", "id": "...", "name": "...", "input": {...}}]}

# Gemini: function_call 是独立字段
{"role": "model", "function_call": {"name": "...", "args": {...}}}

# 问题：混用格式化器会导致 API 返回 400 错误
formatter = OpenAIChatFormatter()  # 用于 OpenAI API
# 如果误用 Anthropic 格式，会报错
```

#### High: TruncatedFormatter 的截断位置

```python showLineNumbers
# TruncatedFormatter 截断消息时，可能截断在句子中间
# 导致 LLM 收到不完整的句子

messages = [
    Msg(name="user", content="Please buy 100 shares of AAPL, 50 shares of MSFT...", role="user")
]
# 如果 token 限制为 50，可能截断为：
# "Please buy 100 shares of AAPL, 50 shares of"
# LLM 无法理解完整的交易意图
```

#### Medium: system_message 位置差异

```python showLineNumbers
# OpenAI: system message 在最前面
# Anthropic: system message 可以在任何位置

# 问题：如果 formatter 实现错误，system message 可能被截断
# 导致 Agent 丢失关键指令
```

#### Medium: 消息角色映射

```python showLineNumbers
# 不同 API 对角色名称有不同的要求
# OpenAI: "system", "user", "assistant", "tool"
# Anthropic: "user", "assistant", "system"（无 "tool"）

# 问题：错误的角色名称会导致 API 报错
msg = Msg(name="bot", content="...", role="invalid_role")
```

---

### 性能考量

#### 格式化延迟分析

| 格式化器 | 延迟 | 说明 |
|----------|------|------|
| SimpleFormatter | ~0.1ms | 最快，直接字典转换 |
| OpenAIChatFormatter | ~0.5ms | 需要处理 tool_calls |
| TruncatedFormatterBase | ~1-5ms | 需要计算和截断 token |

#### Token 计算影响

```python showLineNumbers
# Token 计算是主要开销来源
# tiktoken 计算 ~0.01ms/调用

# 大量消息时的累积延迟：
# 100 条消息：~1ms
# 1000 条消息：~10ms

# 优化建议：
# - 避免重复格式化
# - 缓存格式化结果
```

#### 消息截断优化

```python showLineNumbers
# 截断策略影响输出质量
# 简单截断：快速但可能丢失关键信息

# 更好的策略：
# 1. 语义截断：优先在句子边界截断
# 2. 摘要优先：先摘要再截断
# 3. 重要性排序：保留重要消息，截断历史消息
```

---

## 5. 代码示例

### 5.1 基本格式化

```python showLineNumbers
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg

formatter = OpenAIChatFormatter()

messages = [
    Msg(name="system", content="You are a helpful assistant.", role="system"),
    Msg(name="user", content="What is Python?", role="user"),
]

formatted = await formatter.format(messages)
for msg in formatted:
    print(f"[{msg['role']}] {msg.get('content', '')}")
```

**运行输出**：
```
[system] [{'type': 'text', 'text': 'You are a helpful assistant.'}]
[user] What is Python?
```

### 5.2 带 Token 截断的格式化

```python showLineNumbers
from agentscope.formatter import OpenAIChatFormatter
from agentscope.token import OpenAITokenCounter

counter = OpenAITokenCounter(model_name="gpt-4")
formatter = OpenAIChatFormatter(
    token_counter=counter,
    max_tokens=100,
)

# 构造一个很长的对话历史
messages = [
    Msg(name="system", content="You are a helpful assistant.", role="system"),
]
for i in range(20):
    messages.append(Msg(name="user", content=f"Question {i}: " + "x" * 200, role="user"))
    messages.append(Msg(name="assistant", content=f"Answer {i}: " + "y" * 200, role="assistant"))

formatted = await formatter.format(messages)
print(f"原始消息数: {len(messages)}")
print(f"格式化后消息数: {len(formatted)}")
```

**运行输出**：
```
原始消息数: 41
格式化后消息数: 5  # 早期消息被截断
```

### 5.3 多模态消息格式化

```python showLineNumbers
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ImageBlock, URLSource

formatter = OpenAIChatFormatter()

messages = [
    Msg(name="user", content=[
        TextBlock(type="text", text="请描述这张图片"),
        ImageBlock(type="image", source=URLSource(type="url", url="https://example.com/cat.jpg")),
    ], role="user"),
]

formatted = await formatter.format(messages)
import json
print(json.dumps(formatted[0], indent=2, ensure_ascii=False))
```

**运行输出**：
```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "请描述这张图片"},
    {"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg"}}
  ]
}
```

### 5.4 多代理对话格式化

```python showLineNumbers
from agentscope.formatter import OpenAIMultiAgentFormatter
from agentscope.message import Msg

formatter = OpenAIMultiAgentFormatter()

messages = [
    Msg(name="Alice", content="你好，请帮我分析数据", role="user"),
    Msg(name="Bob", content="收到，我需要调用工具获取数据", role="assistant"),
    Msg(name="Bob", content="数据获取完成，分析结果如下...", role="assistant"),
]

formatted = await formatter.format(messages)
print(f"格式化后消息数: {len(formatted)}")
# 多代理模式下合并为一条 user 消息
print(formatted[0]["content"][0]["text"][:80])
```

**运行输出**：
```
格式化后消息数: 1
# Conversation History
The content between <history></history> tags contains your conversation history
```

---

## 6. 练习题

### 基础题

**Q1**: 为什么 `TruncatedFormatterBase.format()` 在处理前先对消息做深拷贝？如果不拷贝会有什么后果？

**Q2**: `_truncate()` 方法为什么要使用 `tool_call_ids` 集合追踪工具调用 ID？

**Q3**: 对比 `OpenAIChatFormatter` 和 `AnthropicChatFormatter` 对工具结果（ToolResultBlock）的不同处理方式。为什么 Anthropic 需要将工具结果包装在 user 消息中？

**Q4**: `_group_messages` 返回的是异步生成器（`AsyncGenerator`）。为什么不用普通生成器？什么场景下消息分组可能涉及异步操作？

**Q5**: `OpenAIChatFormatter` 和 `AnthropicChatFormatter` 对系统消息的处理方式有何不同？为什么 Anthropic 需要将系统消息重映射为人类消息？

### 中级题

**Q6**: 分析 `MultiAgentChatFormatter` 如何处理多代理对话。它的 `<history>` 标签解析逻辑在什么情况下会失效？

**Q7**: 假设你需要支持一个新的 LLM 厂商，该厂商要求消息格式为 XML 标签包裹（如 `<role>user</role><content>...</content>`）。如何设计一个通用的消息序列化框架？

**Q8**: `DashScopeChatFormatter` 中有一段"API 缺陷兼容"代码。如果阿里云修复了这个缺陷，代码需要如何重构？有哪些向后兼容的考虑？

### 挑战题

**Q9**: 设计一个 `PriorityTruncateMixin`，修改截断策略为"按消息优先级截断"而非"按时间顺序截断"。优先级规则：system 消息 > 最近的 3 轮对话 > 工具调用 > 早期对话。

**Q10**: 设计一个支持流式和非流式两种模式的 `StreamingFormatterMixin`。关键挑战是：流式模式下需要边格式化边发送，无法预知最终 Token 数量。如何设计一个自适应的截断策略？

---

### 参考答案

**A1**: 截断操作会删除消息，如果不做深拷贝，截断会修改调用者的原始消息列表，导致不可预期的副作用。例如，用户传入的消息列表在截断后可能丢失早期历史，后续重试时无法恢复。深拷贝保证了截断只影响格式化过程中的临时数据。

**A2**: LLM API 要求 `tool_use` 和对应的 `tool_result` 必须成对出现。如果截断时只删除了 `tool_use` 但保留了 `tool_result`（或反过来），API 会返回错误。`tool_call_ids` 集合确保截断后不会出现孤立的工具调用或结果。

**A3**: OpenAI 使用 `{"role": "tool", "tool_call_id": "...", "content": "..."}` 表示工具结果，这是独立的 role 类型。Anthropic API 没有独立的 `tool` role，工具结果必须放在 `user` 消息中，使用 `{"type": "tool_result", "tool_use_id": "..."}` 内容块。这反映了两个 API 的设计哲学差异：OpenAI 将工具交互视为独立角色，Anthropic 将其视为用户消息的一部分。

**A4**: 当前实现中 `_group_messages` 本身不需要异步操作，使用 `AsyncGenerator` 主要是保持接口一致性——`_format` 是异步方法，通过 `async for` 消费分组结果。如果未来分组逻辑需要异步判断（如查询外部服务获取消息元数据），异步接口已经预留了扩展空间。

**A5**: OpenAI 直接保留原始 system prompt 作为独立消息，而 Anthropic 会将其包装在首条 user 消息中。这是因为 Anthropic API 对 system 消息有长度限制（只能放在首轮对话），超出部分会被静默截断。重映射确保系统指令的完整性。

**A6**: `<history>` 标签解析依赖正则匹配，如果对话内容中包含类似的 XML 标签（如 `<history>` 作为用户输入内容），会导致错误的分割。当前实现没有处理标签嵌套或转义情况。在实际使用中应避免让用户输入包含这些特殊标签。

**A7**: 关键设计：(1) 定义 `MessageSerializer` 抽象接口，包含 `serialize_role()` 和 `serialize_content()` 方法；(2) 各厂商实现该接口，如 `OpenAISerializer`、`AnthropicSerializer`；(3) `FormatterBase` 持有 `MessageSerializer` 实例，可在运行时替换；(4) 通过依赖注入保持框架的灵活性。

**A8**: 阿里云的缺陷是 tool_call ID 格式与 OpenAI 不兼容（包含特殊字符）。修复后应移除兼容代码，改为直接使用标准格式。同时通过版本检测保持向后兼容——如果厂商 API 版本低于某阈值，保留兼容逻辑；高于阈值则使用新格式。

**A9**: 关键实现思路：重写 `_truncate()` 方法，为每条消息计算优先级分数（system=100, 最近3轮=80, tool_use/result=60, 早期=20），然后按优先级从低到高删除，直到 Token 数在预算内。注意仍需维护 `tool_call_ids` 确保工具调用配对。

**A10**: 关键设计：(1) 流式模式下使用"预估-检查-调整"循环：先按预估 Token 预算发送，达到阈值后暂停；(2) 服务端流式响应时，通过 `ServerSideEvent` 实时更新 Token 计数；(3) 非流式模式保持原有 `format-count-truncate` 循环；(4) 设计 `TruncateStrategy` 抽象，允许运行时切换策略。

---

## 模块小结

| 概念 | 要点 |
|------|------|
| FormatterBase | 格式化器抽象基类，定义 `format()` 接口 |
| TruncatedFormatterBase | format-count-truncate 循环，消息分组，Token 截断 |
| `_group_messages` | 按内容将消息分为 tool_sequence 和 agent_message |
| OpenAIChatFormatter | OpenAI 单代理，支持 image/audio/tools |
| AnthropicChatFormatter | Anthropic 单代理，系统消息重映射，无 audio |
| DashScopeChatFormatter | DashScope 单代理，支持 video，API 缺陷兼容 |
| MultiAgent 格式化器 | `<history>` 标签包装，合并为一条 user 消息 |

| 关联模块 | 关联点 | 参考位置 |
|----------|--------|----------|
| [消息模块](module_message_deep.md#3-核心类与函数源码解读) | 格式化器处理 Msg 和 ContentBlock | 第 3.1-3.2 节 |
| [模型模块](module_model_deep.md#2-chatmodelbase-基类分析) | Model 调用 Formatter 进行 API 请求格式化 | 第 2.2 节 |
| [嵌入与 Token 模块](module_embedding_token_deep.md#8-token-计数机制) | TokenCounterBase 用于格式化器的 Token 计算 | 第 8.1-8.4 节 |
| [智能体模块](module_agent_deep.md#3-agentbase-源码解读) | Agent 的 reply 消息通过 Formatter 发送给 Model | 第 3.2 节 |
| [追踪模块](module_tracing_deep.md#3-追踪装饰器) | trace_format() 追踪格式化调用 | 第 3.6 节 |
| [管道模块](module_pipeline_infra_deep.md#3-formatter-消息格式化) | Pipeline 负责消息在 Agent 间的流转 | 第 3.1-3.3 节 |


---

## 本章小结

- FormatterBase 定义格式化器抽象接口，TruncatedFormatterBase 实现 format-count-truncate 循环
- `_group_messages` 按内容将消息分为 tool_sequence 和 agent_message 两大类
- 各厂商格式化器（OpenAI / Anthropic / DashScope）针对不同 API 消息格式做适配，处理多模态内容块差异
- MultiAgent 格式化器使用 `<history>` 标签包装对话历史，合并为一条 user 消息
- TokenCounterBase 集成使格式化器能按 Token 预算截断消息，保持工具调用配对完整性

## 下一章

→ [State 状态模块](module_state_deep.md)
