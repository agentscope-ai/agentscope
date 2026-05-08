# 第四章：核心概念

## 学习目标

> 学完本节，你将能够：
> - [L2 理解] 解释 Agent、Model、Tool、Memory 四大核心概念及其关系
> - [L3 应用] 使用正确的参数创建 ReActAgent 并注册工具
> - [L4 分析] 比较 Pipeline 编排模式的适用场景

**预计时间**：30 分钟
**先修要求**：已完成 [第三章：快速入门](03_quickstart.md)

## 4.1 概念总览

AgentScope 有四个核心概念，理解它们就掌握了框架的精髓：

```
┌─────────────────────────────────────────────────────────────────┐
│                         核心概念                                  │
│                                                                 │
│  ┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐  │
│  │  Agent  │────▶│  Model  │────▶│  Tool   │────▶│ Memory  │  │
│  │ 智能体  │     │  模型   │     │  工具   │     │  记忆   │  │
│  └─────────┘     └─────────┘     └─────────┘     └─────────┘  │
│       │                                                   │     │
│       └─────────────────── MsgHub ───────────────────────┘     │
│                          (消息中心)                              │
└─────────────────────────────────────────────────────────────────┘
```

## 4.2 Agent (智能体)

### 什么是 Agent

**Agent = LLM + 推理引擎 + 工具 + 记忆**

```python showLineNumbers
# Agent 的简化结构 (伪代码)
class Agent:
    def __init__(self, name, model, tools, memory):
        self.name = name
        self.model = model        # LLM 大脑
        self.tools = tools       # 工具箱
        self.memory = memory     # 记忆系统
        self.reasoning_engine = ReActEngine()  # 推理引擎
```

### Agent 的生命周期

```
┌─────────────────────────────────────────────────────────────┐
│ Agent 生命周期                                                │
│                                                              │
│  1. receive(user_input)                                      │
│         ↓                                                    │
│  2. think() ───▶ 思考用户意图，决定是否使用工具                  │
│         ↓                                                    │
│  3. act() ─────▶ 执行工具或直接回复                            │
│         ↓                                                    │
│  4. observe() ──▶ 获取工具执行结果                             │
│         ↓                                                    │
│  5. respond() ──▶ 生成最终回复                                 │
│                                                              │
│  类比 Java Servlet 生命周期: init() → service() → destroy()   │
└─────────────────────────────────────────────────────────────┘
```

### ReActAgent 详解

ReAct = **Re**asoning + **Act**ing

```python showLineNumbers
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit
from agentscope.formatter import OpenAIChatFormatter

# 创建 Toolkit 并注册工具
toolkit = Toolkit()
toolkit.register_tool_function(tool_func=my_tool_func, group_name="basic")

my_agent = ReActAgent(
    name="助手",           # Agent 名称 (必填)
    sys_prompt="你是一个有帮助的助手",  # 系统提示词（必填）
    model=OpenAIChatModel(model_name="gpt-4o"),  # 模型（必填）
    formatter=OpenAIChatFormatter(),  # 消息格式化器（必填）
    toolkit=toolkit,       # 工具箱（可选）
    memory=None,            # 记忆模块（可选）
)
```

### Agent 源码核心设计

#### AgentBase 抽象基类 (`_agent_base.py`)

`AgentBase` 是所有 Agent 的抽象基类，核心设计要点：

```
┌─────────────────────────────────────────────────────────────┐
│                     AgentBase 核心架构                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                    消息处理                            │    │
│  │  reply() ────▶ 生成回复                               │    │
│  │  observe() ──▶ 接收消息不回复                          │    │
│  │  print() ────▶ 消息输出/流式打印                      │    │
│  └─────────────────────────────────────────────────────┘    │
│                              │                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                    Hook 机制                           │    │
│  │  pre_reply / post_reply                              │    │
│  │  pre_observe / post_observe                          │    │
│  │  pre_print / post_print                              │    │
│  └─────────────────────────────────────────────────────┘    │
│                              │                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                    订阅发布机制                         │    │
│  │  _subscribers ──▶ 消息广播                           │    │
│  │  _broadcast_to_subscribers()                        │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**关键方法解析：**

```python showLineNumbers
# _agent_base.py 核心方法

async def reply(self, *args, **kwargs) -> Msg:
    """核心回复方法，子类必须实现"""
    raise NotImplementedError(...)

async def observe(self, msg: Msg | list[Msg] | None) -> None:
    """接收消息但不生成回复，用于订阅其他Agent"""
    raise NotImplementedError(...)

async def print(self, msg: Msg, last: bool = True,
                speech: AudioBlock | None = None) -> None:
    """打印消息，支持流式输出和语音合成"""
    # 1. 如果启用msg_queue，发送消息到队列
    # 2. 处理text和thinking块，累积打印
    # 3. 处理audio/video/image等多模态块
    # 4. 支持TTS语音输出
```

#### Hook 机制详解

Hook系统允许在Agent执行关键方法前后插入自定义逻辑：

```python showLineNumbers
# Hook 类型
supported_hook_types = [
    "pre_reply",    # reply()调用前
    "post_reply",   # reply()调用后
    "pre_print",    # print()调用前
    "post_print",   # print()调用后
    "pre_observe",  # observe()调用前
    "post_observe", # observe()调用后
]

# 注册实例级Hook
agent.register_instance_hook(
    hook_type="pre_reply",
    hook_name="my_pre_hook",
    hook=lambda self, kwargs: kwargs  # 返回修改后的kwargs
)

# 注册类级别Hook（对所有实例生效）
AgentBase.register_class_hook(
    hook_type="post_reply",
    hook_name="log_reply",
    hook=lambda self, kwargs, output: output  # 返回修改后的output
)
```

**应用场景：**
- 日志记录和监控
- 消息过滤和转换
- 性能指标收集
- 调试和追踪

#### 消息订阅广播机制

```python showLineNumbers
# Agent A 注册订阅 Agent B
agent_b.reset_subscribers("msghub_name", [agent_a])

# Agent B 调用时，Agent A 会自动收到 observe() 调用
await agent_b.reply(msg)  # 触发 agent_a.observe(result)
```

#### ReActAgent 完整 ReAct 循环

`ReActAgent` 实现了完整的 ReAct 推理循环：

```
┌─────────────────────────────────────────────────────────────────┐
│                    ReActAgent 推理循环                             │
│                                                                 │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐              │
│  │ Reasoning │ ───▶ │  Acting  │ ───▶ │ Observe  │              │
│  │  (思考)   │      │  (行动)  │      │  (观察)  │              │
│  └────┬─────┘      └────┬─────┘      └────┬─────┘              │
│       │                  │                  │                    │
│       ▼                  ▼                  │                    │
│  ┌──────────┐      ┌──────────┐           │                    │
│  │  LLM     │      │ 执行工具  │           │                    │
│  │  调用    │      │ 或回复    │           │                    │
│  └──────────┘      └──────────┘           │                    │
│                                            │                    │
│       ◀────────────────────────────────────┘                    │
│                      (循环直到完成)                               │
└─────────────────────────────────────────────────────────────────┘
```

**源码核心流程 (`_react_agent.py`):**

```python showLineNumbers
async def reply(self, msg: Msg | list[Msg] | None = None,
                structured_model: Type[BaseModel] | None = None) -> Msg:
    # 1. 记录输入消息到记忆
    await self.memory.add(msg)

    # 2. 从长期记忆检索相关信息
    await self._retrieve_from_long_term_memory(msg)

    # 3. 从知识库检索文档
    await self._retrieve_from_knowledge(msg)

    # 4. ReAct 推理循环 (最多 max_iters 次)
    for _ in range(self.max_iters):
        # 4.1 压缩记忆（如果需要）
        await self._compress_memory_if_needed()

        # 4.2 Reasoning: 调用 LLM 决定动作
        msg_reasoning = await self._reasoning(tool_choice)

        # 4.3 Acting: 执行工具或生成回复
        futures = [self._acting(tool_call)
                   for tool_call in msg_reasoning.get_content_blocks("tool_use")]

        if self.parallel_tool_calls:
            structured_outputs = await asyncio.gather(*futures)
        else:
            structured_outputs = [await f for f in futures]

        # 4.4 检查是否完成
        if not msg_reasoning.has_content_blocks("tool_use"):
            reply_msg = msg_reasoning
            break

    # 5. 后处理：记录到长期记忆
    if self._static_control:
        await self.long_term_memory.record([...])

    return reply_msg
```

#### 记忆压缩机制

当对话历史超过阈值时，自动压缩旧记忆：

```python showLineNumbers
# 压缩配置
compression_config = CompressionConfig(
    enable=True,
    agent_token_counter=token_counter,
    trigger_threshold=4000,  # 超过4000 tokens时压缩
    keep_recent=3,          # 保留最近3条消息
)

# 压缩流程
async def _compress_memory_if_needed(self):
    # 1. 获取未压缩的消息
    to_compressed_msgs = await self.memory.get_memory(
        exclude_mark=_MemoryMark.COMPRESSED
    )

    # 2. 保留最近的 tool_use/result 对
    # (避免ToolCall和结果被分开压缩)

    # 3. 计算 token 数
    n_tokens = await self.compression_config.agent_token_counter.count(prompt)

    # 4. 超过阈值则压缩
    if n_tokens > self.compression_config.trigger_threshold:
        # 使用 LLM 生成压缩摘要
        res = await compression_model(compression_prompt,
                                      structured_model=SummarySchema)
        # 更新记忆，标记已压缩的消息
        await self.memory.update_compressed_summary(summary)
        await self.memory.update_messages_mark(msg_ids, _MemoryMark.COMPRESSED)
```

#### 结构化输出

ReActAgent 支持通过 `generate_response` 工具生成结构化输出：

```python showLineNumbers
from pydantic import BaseModel

class SearchResult(BaseModel):
    title: str
    url: str
    summary: str

# 调用时指定结构化模型
result = await agent.reply(
    msg="搜索 AI 最新的进展",
    structured_model=SearchResult
)
# result.metadata 包含结构化的 SearchResult 数据
```

### Java 对比

| AgentScope | Java |
|------------|------|
| `agent.ReActAgent` | `@Service` class |
| `name` | `@Service("name")` |
| `model` | injected `Repository` |
| `tools` | injected `List<Bean>` |
| `memory` | injected `Cache` |
| `sys_prompt` | 类比 `application.yml` 中的 `spring.application.name` |
| `Hook机制` | Spring AOP `@Before`/`@After` |
| `subscribe/observe` | Observer Pattern / Spring Events |
| `CompressionConfig` | LRU Cache +定期持久化 |

## 4.3 Model (模型)

### 4.3.1 模型架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    Model (模型抽象层)                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              ChatModelBase (抽象基类)                   │    │
│  │  ┌─────────────────────────────────────────────┐    │    │
│  │  │ model_name, stream, __call__() [抽象方法]      │    │    │
│  │  │ _validate_tool_choice()                      │    │    │
│  │  └─────────────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────┘    │
│                            │                                │
│          ┌─────────────────┼─────────────────┐             │
│          ▼                 ▼                 ▼             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│  │ OpenAI      │  │ Anthropic   │  │ DashScope   │       │
│  │ ChatModel   │  │ ChatModel   │  │ ChatModel   │       │
│  └─────────────┘  └─────────────┘  └─────────────┘       │
│          │                 │                 │             │
│          ▼                 ▼                 ▼             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│  │ Gemini      │  │ Ollama      │  │ DeepSeek    │       │
│  │ ChatModel   │  │ ChatModel   │  │ (via OpenAI)│       │
│  └─────────────┘  └─────────────┘  └─────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

### 4.3.2 核心数据结构

AgentScope 定义了三个核心数据结构，用于统一各模型的响应格式：

#### ChatResponse - 模型响应

```python showLineNumbers
@dataclass
class ChatResponse(DictMixin):
    """统一的聊天响应格式"""
    content: Sequence[TextBlock | ToolUseBlock | ThinkingBlock | AudioBlock]
    """响应内容，包含多种块类型"""

    id: str                    # 唯一标识符
    created_at: str           # 创建时间戳
    type: Literal["chat"]     # 响应类型
    usage: ChatUsage | None   # Token 使用统计
    metadata: dict | None      # 额外元数据
```

#### ChatUsage - Token 使用统计

```python showLineNumbers
@dataclass
class ChatUsage(DictMixin):
    """API 调用统计信息"""
    input_tokens: int   # 输入 Token 数
    output_tokens: int # 输出 Token 数
    time: float        # 耗时（秒）
    type: Literal["chat"]
    metadata: dict | None  # 原始使用数据
```

#### 内容块类型

| 块类型 | 用途 | 示例 |
|--------|------|------|
| `TextBlock` | 文本回复 | `TextBlock(type="text", text="Hello!")` |
| `ToolUseBlock` | 工具调用 | `ToolUseBlock(type="tool_use", name="search", input={...})` |
| `ThinkingBlock` | 思考过程 | `ThinkingBlock(type="thinking", thinking="Let me think...")` |
| `AudioBlock` | 音频回复 | `AudioBlock(type="audio", source=Base64Source(...))` |

### 4.3.3 ChatModelBase 抽象基类设计

```python showLineNumbers
# src/agentscope/model/_model_base.py

class ChatModelBase:
    """所有聊天模型的抽象基类"""

    model_name: str  # 模型名称
    stream: bool     # 是否流式输出

    @abstractmethod
    async def __call__(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        structured_model: Type[BaseModel] | None = None,
        **kwargs
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """统一的模型调用接口 - 所有子类必须实现"""

    def _validate_tool_choice(
        self,
        tool_choice: str,
        tools: list[dict] | None,
    ) -> None:
        """验证 tool_choice 参数的合法性"""
        # 支持的模式：["auto", "none", "required"] 或具体函数名
```

**设计亮点**：
- **统一接口**：所有模型实现统一的 `__call__` 方法签名
- **异步设计**：使用 `async/await` 提升并发性能
- **流式支持**：返回 `AsyncGenerator` 处理流式响应
- **结构化输出**：通过 `structured_model` 参数支持 Pydantic 模型

### 4.3.4 模型实现详解

#### OpenAIChatModel

```python showLineNumbers
# 初始化配置
model = OpenAIChatModel(
    model_name="gpt-4o",           # 模型名称
    api_key="sk-xxxxx",            # API Key（可从环境变量读取）
    stream=True,                    # 启用流式输出
    reasoning_effort="medium",     # 推理努力程度 (o3/o4 系列)
    organization="org-xxx",        # 组织 ID（可选）
    stream_tool_parsing=True,       # 流式解析工具参数
    client_type="openai",          # "openai" 或 "azure"
    client_kwargs={},              # 额外的客户端参数
    generate_kwargs={              # 生成参数
        "temperature": 0.7,
        "max_tokens": 2048,
    }
)
```

**核心特性**：
- **Azure OpenAI 支持**：通过 `client_type="azure"` 切换
- **结构化输出**：支持 `response_format=BaseModel` 原生支持
- **流式工具解析**：实时解析不完整的 JSON 字符串

#### AnthropicChatModel

```python showLineNumbers
model = AnthropicChatModel(
    model_name="claude-3-5-sonnet-20241022",
    api_key="sk-ant-xxxxx",
    stream=True,
    max_tokens=2048,              # 必须指定
    thinking={                    # 思考模式配置
        "type": "enabled",
        "budget_tokens": 1024
    },
    stream_tool_parsing=True,
)
```

**思考模式**：Claude 的 extended thinking 功能允许模型在生成最终答案前进行内部推理。

#### DashScopeChatModel (阿里云通义千问)

```python showLineNumbers
model = DashScopeChatModel(
    model_name="qwen-plus",        # 或 "qvq-max", "qwen2.5-72b-instruct"
    api_key="sk-xxxxx",           # 阿里云 API Key
    stream=True,
    enable_thinking=True,          # 启用思考模式（Qwen3, DeepSeek-R1）
    multimodality=None,            # 自动检测：图像/视频用 MultiModalConversation
    generate_kwargs={
        "temperature": 0.7,
        "seed": 42,
    }
)
```

**智能 API 选择**：
- 自动检测模型名包含 `-vl` 或以 `qvq` 开头
- 自动选择 `MultiModalConversation` 或 `Generation` API

#### GeminiChatModel

```python showLineNumbers
model = GeminiChatModel(
    model_name="gemini-2.5-flash",
    api_key="xxxxx",
    stream=True,
    thinking_config={              # 思考配置
        "include_thoughts": True,
        "thinking_budget": 1024
    }
)
```

**特殊处理**：
- **`$ref` 解析**：Gemini 不支持 JSON Schema 引用，自动内联展开
- **Function Calling**：通过 `tool_config` 配置

#### OllamaChatModel (本地模型)

```python showLineNumbers
model = OllamaChatModel(
    model_name="qwen2.5:7b",      # 本地模型名
    stream=True,
    host="http://localhost:11434", # Ollama 服务地址
    options={                     # 模型参数
        "temperature": 0.7,
        "num_ctx": 4096,         # 上下文窗口
    },
    keep_alive="5m",             # 模型在内存中保持时间
    enable_thinking=True,        # 部分本地模型支持思考
)
```

**优势**：完全本地运行，无 API 费用，支持自定义模型

### 4.3.5 工具调用 (Function Calling)

所有模型统一支持工具调用，格式略有不同：

```python showLineNumbers
# 定义工具
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取城市天气",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名"}
                },
                "required": ["city"]
            }
        }
    }
]

# 调用模型
response = await model(messages, tools=tools)

# 处理工具调用
for block in response.content:
    if isinstance(block, ToolUseBlock):
        print(f"调用工具: {block.name}")
        print(f"参数: {block.input}")
```

**tool_choice 选项**：

| 值 | 说明 |
|-----|------|
| `"auto"` | 模型自动决定是否调用工具 |
| `"none"` | 不调用任何工具 |
| `"required"` | 必须调用一个工具 |
| `"具体函数名"` | 强制调用指定工具 |

### 4.3.6 结构化输出 (Structured Output)

使用 Pydantic 模型强制约束模型输出格式：

```python showLineNumbers
from pydantic import BaseModel

class WeatherResponse(BaseModel):
    city: str
    temperature: float
    condition: str
    humidity: int

# 使用结构化输出
response = await model(
    messages,
    structured_model=WeatherResponse
)

# 访问结构化数据
weather = response.metadata  # 直接得到 Pydantic 对象
print(f"{weather.city}: {weather.temperature}°C")
```

**实现机制**：
1. OpenAI: 使用原生 `response_format=BaseModel`
2. 其他模型：通过强制工具调用实现回退方案

### 4.3.7 流式响应处理

```python showLineNumbers
# 基础流式处理
async for chunk in model(messages):
    # 每个 chunk 都是一个 ChatResponse
    for block in chunk.content:
        if isinstance(block, TextBlock):
            print(block.text, end="", flush=True)
        elif isinstance(block, ThinkingBlock):
            print(f"
[思考: {block.thinking[:50]}...]", end="")

# 带工具调用的流式处理
accumulated_tool_calls = []
async for chunk in model(messages, tools=tools):
    for block in chunk.content:
        if isinstance(block, ToolUseBlock):
            accumulated_tool_calls.append(block)
    if chunk.usage:
        print(f"
[Token: {chunk.usage.output_tokens}]")
```

### 4.3.8 错误处理与重试机制

```python showLineNumbers
from agentscope._logging import logger

try:
    response = await model(messages)
except RuntimeError as e:
    logger.error(f"API 调用失败: {e}")
    # 实现重试逻辑
    for attempt in range(3):
        try:
            response = await model(messages)
            break
        except Exception as retry_error:
            if attempt == 2:
                raise
            logger.warning(f"重试 {attempt + 1}/3...")
```

**常见错误类型**：

| 错误类型 | 原因 | 处理方式 |
|----------|------|----------|
| `RuntimeError` | API 返回非 200 状态码 | 检查网络/重试 |
| `ValueError` | 无效参数 | 检查输入格式 |
| `ImportError` | 缺少依赖包 | `pip install openai` 等 |

### 4.3.9 成本优化策略

#### 1. 选择合适的模型

```python showLineNumbers
# 简单任务用小模型
fast_model = OpenAIChatModel(model_name="gpt-4o-mini")

# 复杂任务用大模型
capable_model = OpenAIChatModel(model_name="gpt-4o")
```

#### 2. 控制 Token 数量

```python showLineNumbers
# 限制最大输出
model = OpenAIChatModel(
    model_name="gpt-4o",
    generate_kwargs={"max_tokens": 500}  # 限制输出
)
```

#### 3. 使用缓存

```python showLineNumbers
# Ollama 本地运行零 API 成本
model = OllamaChatModel(model_name="llama3:8b")

# DashScope 阿里云内网调用
model = DashScopeChatModel(
    model_name="qwen-plus",
    base_http_api_url="http://vpc.internal.api"  # 内网地址
)
```

#### 4. 批量处理

```python showLineNumbers
import asyncio

async def batch_process(queries: list[str]):
    tasks = [model([{"role": "user", "content": q}]) for q in queries]
    return await asyncio.gather(*tasks)

# 并发处理多个请求
results = await batch_process(["问题1", "问题2", "问题3"])
```

### 4.3.10 完整使用示例

```python showLineNumbers
import asyncio
from agentscope.model import OpenAIChatModel, ChatResponse
from agentscope.message import TextBlock, ToolUseBlock

async def main():
    # 初始化模型
    model = OpenAIChatModel(
        model_name="gpt-4o",
        api_key="sk-xxxxx",
        stream=True
    )

    # 定义工具
    tools = [{
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "执行数学计算",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string"}
                },
                "required": ["expression"]
            }
        }
    }]

    messages = [{"role": "user", "content": "计算 (15 + 23) * 7"}]

    # 调用模型
    print("Assistant: ", end="")
    async for response in model(messages, tools=tools):
        for block in response.content:
            if isinstance(block, TextBlock):
                print(block.text, end="", flush=True)
            elif isinstance(block, ToolUseBlock):
                print(f"
[工具调用: {block.name}]")

        # 打印使用统计
        if response.usage:
            print(f"
[Token: {response.usage.output_tokens}]")

asyncio.run(main())
```

### 4.3.11 Java 对比

| AgentScope | Java |
|------------|------|
| `OpenAIChatModel` | `OpenAIModel` |
| `ChatResponse` | `CompletableFuture<ChatResult>` |
| `structured_model` | Jackson @JsonSchema |
| 流式响应 | Reactive Streams `Publisher` |
| `tools` | `@Tool` 注解 |
| `stream_tool_parsing` | 手动 JSON 解析 |
| `reasoning_effort` | 无直接对应 |



## 4.4 Tool (工具)

### 4.4.1 工具架构概述

```
┌─────────────────────────────────────────────────────────────────┐
│                      Tool 工具系统                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Toolkit (核心管理类)                   │   │
│  │  - register_tool_function() 注册工具函数                   │   │
│  │  - call_tool_function()  执行工具函数                     │   │
│  │  - get_json_schemas()    获取工具 JSON Schema             │   │
│  │  - register_mcp_client() 注册 MCP 客户端工具              │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                 │
│          ┌──────────────────┼──────────────────┐             │
│          ▼                  ▼                  ▼             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐       │
│  │RegisteredTool│    │ MCPToolFunc │    │ AgentSkill  │       │
│  │  Function   │    │  (MCP工具)  │    │  (技能)     │       │
│  └─────────────┘    └─────────────┘    └─────────────┘       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 4.4.2 源码核心文件

| 文件 | 职责 |
|------|------|
| `tool/_toolkit.py` | 核心管理类 `Toolkit` (1600+ 行) |
| `tool/_types.py` | 类型定义 `RegisteredToolFunction`, `ToolGroup` |
| `tool/_response.py` | `ToolResponse` 结果类 |
| `tool/_async_wrapper.py` | 流式响应包装器 |
| `_utils/_common.py` | `_parse_tool_function()` 签名解析 |
| `mcp/_mcp_function.py` | `MCPToolFunction` MCP工具包装类 |
| `mcp/_client_base.py` | `MCPClientBase` 抽象基类 |
| `mcp/_stateful_client_base.py` | 有状态 MCP 客户端基类 |
| `mcp/_http_stateful_client.py` | HTTP 有状态 MCP 客户端 |
| `mcp/_http_stateless_client.py` | HTTP 无状态 MCP 客户端 |
| `mcp/_stdio_stateful_client.py` | STDIO 有状态 MCP 客户端 |

### 4.4.3 工具核心类型

```python showLineNumbers
# ToolResponse - 工具执行结果
@dataclass
class ToolResponse:
    content: List[TextBlock | ImageBlock | AudioBlock | VideoBlock]
    metadata: Optional[dict]
    stream: bool = False
    is_last: bool = True
    is_interrupted: bool = False

# RegisteredToolFunction - 注册的工具函数
@dataclass
class RegisteredToolFunction:
    name: str
    group: str | Literal["basic"]
    source: Literal["function", "mcp_server", "function_group"]
    original_func: ToolFunction
    json_schema: dict
    preset_kwargs: dict
    extended_model: Type[BaseModel] | None
    postprocess_func: Callable
```

### 4.4.4 内置工具

```python showLineNumbers
from agentscope.tool import (
    Toolkit,
    execute_python_code, execute_shell_command,
    view_text_file, write_text_file, insert_text_file,
    dashscope_text_to_image, dashscope_image_to_text,
)

# 创建 Toolkit 并注册内置工具
toolkit = Toolkit()
toolkit.register_tool_function(tool_func=execute_python_code, group_name="basic")
toolkit.register_tool_function(tool_func=execute_shell_command, group_name="basic")

my_agent = ReActAgent(
    name="代码助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    toolkit=toolkit,
    sys_prompt="你是一个代码执行助手",
    formatter=OpenAIChatFormatter(),
)
```

### 4.4.5 自定义工具开发

AgentScope 通过 **自动签名解析** 实现工具注册，无需装饰器：

```python showLineNumbers
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

def search_database(query: str, table: str = "users") -> ToolResponse:
    """在数据库中搜索记录

    Args:
        query: 搜索关键词
        table: 表名，默认 'users'
    """
    results = [{"id": 1, "name": "张三"}]
    return ToolResponse(content=[TextBlock(type="text", text=json.dumps(results))])

toolkit = Toolkit()
toolkit.register_tool_function(tool_func=search_database, group_name="basic")
```

### 4.4.6 工具注册机制

`register_tool_function()` 核心参数：

```python showLineNumbers
toolkit.register_tool_function(
    tool_func=my_function,           # 工具函数
    group_name="basic",              # 所属组
    func_name="custom_name",        # 自定义函数名
    func_description="描述",        # 自定义描述
    preset_kwargs={"api_key": "xxx"},  # 预设参数（不暴露给 LLM）
    postprocess_func=my_postprocess, # 后处理函数
    namesake_strategy="raise",       # 重名策略
)
```

### 4.4.7 JSON Schema 生成

AgentScope 自动从函数签名和 docstring 生成 JSON Schema：

```python showLineNumbers
def complex_tool(user_id: int, name: str = "default",
                 tags: list[str] = None) -> ToolResponse:
    """复杂参数示例工具
    Args:
        user_id: 用户ID（必填）
        name: 用户名（可选）
        tags: 用户标签列表
    """
    pass
```

生成的 Schema：

```json
{
  "type": "function",
  "function": {
    "name": "complex_tool",
    "parameters": {
      "properties": {
        "user_id": {"type": "integer"},
        "name": {"type": "string", "default": "default"},
        "tags": {"type": "array", "items": {"type": "string"}}
      },
      "required": ["user_id"]
    }
  }
}
```

### 4.4.8 工具组管理

```python showLineNumbers
toolkit.create_tool_group(group_name="database", description="数据库操作", active=False)
toolkit.update_tool_groups(["database"], active=True)
toolkit.reset_equipped_tools(database=True, web_search=False)
```

### 4.4.9 流式工具执行

```python showLineNumbers
async def streaming_tool() -> AsyncGenerator[ToolResponse, None]:
    for i in range(5):
        yield ToolResponse(content=[TextBlock(type="text", text=f"Step {i}")], stream=True)
    yield ToolResponse(content=[TextBlock(type="text", text="Done!")], stream=True, is_last=True)
```

### 4.4.10 MCP 协议工具

```python showLineNumbers
from agentscope.mcp import HttpStatefulClient

mcp_client = HttpStatefulClient(name="filesystem", transport="streamable_http",
                                url="http://localhost:3000/mcp")
await mcp_client.connect()
await toolkit.register_mcp_client(mcp_client=mcp_client, group_name="filesystem",
                                 enable_funcs=["read_file", "write_file"])
await mcp_client.close()
```

### 4.4.11 中间件机制

```python showLineNumbers
async def logging_middleware(kwargs, next_handler):
    tool_call = kwargs["tool_call"]
    print(f"Calling tool: {tool_call['name']}")
    async for response in await next_handler(**kwargs):
        yield response

toolkit.register_middleware(logging_middleware)
```

### 4.4.12 完整示例

```python
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock, ToolUseBlock
import asyncio, json

def search_database(query: str, table: str = "users") -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=f"Results for {query}")])

def get_weather(city: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=f"Weather in {city}: Sunny")])

toolkit = Toolkit()
toolkit.register_tool_function(tool_func=search_database, group_name="database")
toolkit.register_tool_function(tool_func=get_weather, group_name="basic")

async def main():
    tool_call = ToolUseBlock(type="tool_use", id="call_123",
                              name="get_weather", input={"city": "Beijing"})
    async for response in toolkit.call_tool_function(tool_call):
        print(response)

asyncio.run(main())
```

### Java 对比

```java
// Java: 手动定义工具接口
public interface Tool {
    String execute(Map<String, Object> params);
    Map<String, ParamSchema> getParameters();
}

@Component("searchDb")
public class SearchDatabaseTool implements Tool {
    @Override
    public String execute(Map<String, Object> params) {
        String query = (String) params.get("query");
        // 执行搜索...
    }
}
```

```python
# Python: AgentScope 自动从签名和 docstring 提取参数信息
def search_database(query: str, table: str = "users") -> ToolResponse:
    # 无需额外配置，自动完成参数解析和验证
    ...
```

## 4.5 Memory (记忆)

### 4.5.1 记忆类型

```
┌─────────────────────────────────────────────────────────┐
│                      记忆系统                             │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │              Working Memory (短期记忆)            │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌───────┐ │   │
│  │  │InMemory │ │  Redis  │ │SQLAlchemy│ │Tablestore│ │   │
│  │  └─────────┘ └─────────┘ └─────────┘ └───────┘ │   │
│  └─────────────────────────────────────────────────┘   │
│                         ↕                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │             Long-Term Memory (长期记忆)           │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐           │   │
│  │  │  Mem0   │ │  ReMe   │ │向量存储 │           │   │
│  │  └─────────┘ └─────────┘ └─────────┘           │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 4.5.2 记忆基类设计

所有记忆实现都继承自 `MemoryBase` 抽象基类，定义统一的接口：

```python showLineNumbers
# 核心抽象方法
class MemoryBase(StateModule):
    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
    ) -> None:
        """添加消息到记忆存储"""

    async def delete(self, msg_ids: list[str]) -> int:
        """根据ID删除消息"""

    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        prepend_summary: bool = True,
    ) -> list[Msg]:
        """获取记忆消息"""

    async def size(self) -> int:
        """获取记忆数量"""

    async def clear(self) -> None:
        """清空所有记忆"""
```

**关键设计：Marks 机制**

Marks 是消息的标签系统，支持灵活的消息分类和过滤：

```python showLineNumbers
# 添加带标记的消息
await memory.add(Msg("user", "我喜欢喝茶", "user"), marks=["偏好", "饮食"])

# 获取带有特定标记的消息
msgs = await memory.get_memory(mark="偏好")

# 排除特定标记的消息
msgs = await memory.get_memory(exclude_mark="临时")

# 更新消息标记
await memory.update_messages_mark(
    new_mark="重要",
    old_mark="临时",
    msg_ids=["msg_id_1", "msg_id_2"]
)
```

### 4.5.3 短期记忆实现

#### InMemoryMemory (开发调试用)

基于 Python 列表的内存实现，适合开发和测试：

```python showLineNumbers
from agentscope.memory import InMemoryMemory

memory = InMemoryMemory()
await memory.add(Msg("user", "Hello", "user"))
msgs = await memory.get_memory()  # 获取所有消息
```

**特点**：
- 进程内存储，访问速度最快
- 不支持跨进程共享
- 重启后数据丢失

#### RedisMemory (生产环境推荐)

基于 Redis 的分布式记忆实现：

```python showLineNumbers
from agentscope.memory import RedisMemory

memory = RedisMemory(
    session_id="user_123_session",
    user_id="user_123",
    host="localhost",
    port=6379,
    db=0,
    key_prefix="myapp:",        # 多租户隔离
    key_ttl=3600,               # 可选：滑动过期时间
)
```

**架构设计**：

```
Redis Key 结构:
┌─────────────────────────────────────────────────────────┐
│  user_id:{user_id}:session:{session_id}:messages       │
│  → Redis List: 按顺序存储消息ID                          │
│                                                         │
│  user_id:{user_id}:session:{session_id}:msg:{msg_id}   │
│  → Redis String: 存储消息JSON payload                   │
│                                                         │
│  user_id:{user_id}:session:{session_id}:mark:{mark}    │
│  → Redis List: 存储带特定标记的消息ID                    │
│                                                         │
│  user_id:{user_id}:session:{session_id}:marks_index   │
│  → Redis Set: 高效索引所有标记名称                      │
└─────────────────────────────────────────────────────────┘
```

**性能优化**：
- 使用 Redis Pipeline 批量操作，减少网络往返
- `mget` 批量获取消息，避免 N+1 查询
- Marks Index 避免扫描所有 key

#### AsyncSQLAlchemyMemory (关系型数据库存储)

支持 SQLite、PostgreSQL、MySQL 等关系数据库：

```python showLineNumbers
from sqlalchemy.ext.asyncio import create_async_engine
from agentscope.memory import AsyncSQLAlchemyMemory

engine = create_async_engine("sqlite+aiosqlite:///memory.db")
memory = AsyncSQLAlchemyMemory(
    engine_or_session=engine,
    session_id="user_session",
    user_id="user_123"
)
```

**数据库表结构**：

```sql
-- 用户表
CREATE TABLE users (id VARCHAR(255) PRIMARY KEY);

-- 会话表
CREATE TABLE session (
    id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255) REFERENCES users(id)
);

-- 消息表
CREATE TABLE message (
    id VARCHAR(255) PRIMARY KEY,  -- 格式: {user_id}-{session_id}-{msg_id}
    msg JSON NOT NULL,
    session_id VARCHAR(255) REFERENCES session(id),
    index BIGINT NOT NULL,         -- 消息顺序
    INDEX idx_session_index (session_id, index)
);

-- 消息标记表
CREATE TABLE message_mark (
    msg_id VARCHAR(255) REFERENCES message(id) ON DELETE CASCADE,
    mark VARCHAR(255),
    PRIMARY KEY (msg_id, mark)
);
```

**性能优化**：
- 索引优化：`(session_id, index)` 复合索引加速消息顺序检索
- 异步 IO：使用 `async_sessionmaker` 支持高并发
- 批量写入：`_get_next_index()` 减少查询次数

### 4.5.4 长期记忆实现

长期记忆用于跨会话、跨 Agent 的持久化信息存储。

#### Mem0LongTermMemory

基于 Mem0 AI 的长期记忆实现，支持语义搜索：

```python showLineNumbers
from agentscope.memory import Mem0LongTermMemory
from agentscope.embedding import OpenAITextEmbedding
from agentscope.model import OpenAIChatModel

memory = Mem0LongTermMemory(
    agent_name="my_agent",
    user_name="user_123",
    model=OpenAIChatModel(model_name="gpt-4"),
    embedding_model=OpenAITextEmbedding(model_name="text-embedding-3-small"),
)

# Agent 可以调用工具记录和检索记忆
# 记录记忆
await memory.record_to_memory(
    thinking="用户提到了他的工作地点，应该记住",
    content=["用户在北京工作", "公司名叫字节跳动"]
)

# 检索记忆
result = await memory.retrieve_from_memory(keywords=["工作", "北京"])
```

**Mem0 架构特点**：
- 自动从对话中提取语义信息
- 支持向量相似度搜索
- 多层回退策略确保记忆持久化

#### ReMeLongTermMemory (基于 ReMe 库)

ReMe (Reasoning on Memory) 是模型Scope开源的记忆框架：

```python showLineNumbers
from agentscope.memory import (
    ReMePersonalLongTermMemory,    # 个人偏好记忆
    ReMeToolLongTermMemory,       # 工具使用记忆
    ReMeTaskLongTermMemory,       # 任务执行记忆
)
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.model import DashScopeChatModel

# 个人记忆
personal_memory = ReMePersonalLongTermMemory(
    agent_name="assistant",
    user_name="user_workspace",
    model=DashScopeChatModel(model_name="qwen-max"),
    embedding_model=DashScopeTextEmbedding(model_name="text-embedding-v3"),
)

# 在异步上下文中使用
async with personal_memory:
    await personal_memory.record_to_memory(
        thinking="用户分享了他的旅行偏好",
        content=["喜欢海边度假", "每年去一次日本"]
    )

    result = await personal_memory.retrieve_from_memory(
        keywords=["旅行偏好", "日本"]
    )
```

**ReMe 记忆类型**：

| 类型 | 用途 | 记录时机 |
|------|------|----------|
| `ReMePersonalLongTermMemory` | 用户偏好、习惯、个人信息 | 用户分享个人信息时 |
| `ReMeToolLongTermMemory` | 工具使用经验、最佳实践 | 工具执行成功后 |
| `ReMeTaskLongTermMemory` | 任务执行经验、解决方案 | 任务完成时 |

### 4.5.5 记忆管理最佳实践

#### 1. 分层记忆架构

```
┌─────────────────────────────────────────────────────────┐
│                    Agent                                 │
│  ┌─────────────────────────────────────────────────┐   │
│  │  System Prompt + 检索到的长期记忆                   │   │
│  └─────────────────────────────────────────────────┘   │
│                         ↑                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Working Memory (短期记忆)                         │   │
│  │  - 最近对话上下文                                  │   │
│  │  - 当前会话重要信息                                 │   │
│  └─────────────────────────────────────────────────┘   │
│                         ↑                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Long-Term Memory (长期记忆)                       │   │
│  │  - 用户偏好、跨会话信息                             │   │
│  │  - 知识库检索结果                                   │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

#### 2. 记忆容量管理

```python showLineNumbers
class MemoryManager:
    """记忆容量管理器"""

    def __init__(self, max_messages: int = 100):
        self.max_messages = max_messages
        self.working_memory = InMemoryMemory()

    async def add_message(self, msg: Msg):
        # 检查容量
        current_size = await self.working_memory.size()
        if current_size >= self.max_messages:
            # 压缩旧消息或转移到长期记忆
            await self.compress_and_offload()

        await self.working_memory.add(msg, marks=["current_session"])

    async def compress_and_offload(self):
        """压缩旧消息并转移到长期记忆"""
        # 获取旧消息
        old_msgs = await self.working_memory.get_memory(
            mark="current_session"
        )[:-10]  # 保留最近10条

        # 记录到长期记忆
        if old_msgs:
            await self.long_term_memory.record(old_msgs)

        # 删除旧消息
        old_ids = [msg.id for msg in old_msgs]
        await self.working_memory.delete(old_ids)
```

#### 3. 消息去重策略

```python showLineNumbers
# InMemoryMemory
await memory.add(msg, allow_duplicates=False)  # 默认去重

# RedisMemory
await memory.add(msg, skip_duplicated=True)    # 默认跳过重复

# SQLAlchemyMemory
await memory.add(msg, skip_duplicated=True)   # 默认跳过重复
```

#### 4. 记忆序列化与恢复

```python showLineNumbers
# 保存记忆状态
state = memory.state_dict()
# {"content": [[msg.to_dict(), ["mark1"]], ...], "_compressed_summary": "..."}

# 恢复记忆状态
memory.load_state_dict(state)

# 实际项目中可保存到文件或数据库
import json
with open("checkpoint.json", "w") as f:
    json.dump(state, f)
```

### 4.5.6 性能对比

| 记忆类型 | 读取延迟 | 写入延迟 | 容量 | 跨进程 | 持久化 |
|----------|----------|----------|------|--------|--------|
| `InMemoryMemory` | ~0.1ms | ~0.1ms | 进程内存 | 否 | 否 |
| `RedisMemory` | ~1-5ms | ~1-5ms | Redis内存/磁盘 | 是 | 是 |
| `AsyncSQLAlchemyMemory` | ~5-20ms | ~10-50ms | 数据库大小 | 是 | 是 |
| `Mem0LongTermMemory` | ~50-200ms | ~100-500ms | 向量存储 | 是 | 是 |

### 4.5.7 Java 对比

| AgentScope | Java |
|------------|------|
| `InMemoryMemory` | `ConcurrentHashMap<String, Object>` |
| `RedisMemory` | Spring Cache + Redis |
| `AsyncSQLAlchemyMemory` | JPA + Hibernate |
| `Mem0` | External AI Memory Service |
| `向量存储` | Elasticsearch / Milvus |

## 4.6 RAG (检索增强生成)

### 4.6.1 RAG 架构

```
┌─────────────────────────────────────────────────────────┐
│                     RAG 系统                              │
│                                                         │
│  ┌───────────────┐     ┌───────────────┐               │
│  │    Reader     │────▶│  Document     │               │
│  │  (读取分块)    │     │  (文档单元)   │               │
│  └───────────────┘     └───────────────┘               │
│          │                     │                         │
│          ▼                     ▼                         │
│  ┌───────────────┐     ┌───────────────┐               │
│  │ Embedding    │────▶│  VDBStore    │               │
│  │   Model      │     │  (向量存储)   │               │
│  └───────────────┘     └───────────────┘               │
│                                 │                        │
│                                 ▼                        │
│  ┌─────────────────────────────────────────────────┐   │
│  │              KnowledgeBase                       │   │
│  │         (检索接口封装)                            │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 4.6.2 核心组件

#### Document 数据结构

```python
@dataclass
class Document:
    id: str                    # 唯一ID
    metadata: DocMetadata      # 元数据
    embedding: Embedding | None  # 向量表示
    score: float | None        # 相似度分数

@dataclass
class DocMetadata:
    content: TextBlock | ImageBlock | VideoBlock  # 内容
    doc_id: str              # 文档ID
    chunk_id: int            # 块编号
    total_chunks: int        # 总块数
```

#### Reader 机制

Reader 负责读取原始文件并切分成 chunks：

```python showLineNumbers
from agentscope.rag import PDFReader, TextReader, WordReader

# PDF 读取器
pdf_reader = PDFReader(chunk_size=512, split_by="sentence")
docs = await pdf_reader("document.pdf")

# 文本读取器
text_reader = TextReader(chunk_size=512, split_by="paragraph")
docs = await text_reader("Some text content...")

# 支持的 Reader 类型：
# - PDFReader: PDF 文件
# - TextReader: 纯文本
# - WordReader: Word 文档 (.docx)
# - ExcelReader: Excel 文件 (.xlsx)
# - ImageReader: 图片 (OCR 提取文字)
# - PPTReader: PowerPoint 文件
```

#### VDBStore 机制

向量存储后端，支持多种向量数据库：

```python showLineNumbers
from agentscope.rag import (
    MilvusLiteStore,      # Milvus Lite (轻量级)
    QdrantStore,          # Qdrant
    MongoDBStore,         # MongoDB
    AlibabaCloudMySQLStore, # 阿里云 MySQL
    OceanBaseStore,       # OceanBase
)

# Milvus Lite 示例
store = MilvusLiteStore(
    collection_name="my_knowledge",
    dimension=1536,  # OpenAI embedding dimension
    metric_type="COSINE",
)

# Qdrant 示例
store = QdrantStore(
    host="localhost",
    port=6333,
    collection_name="my_knowledge",
)
```

### 4.6.3 KnowledgeBase 使用

```python showLineNumbers
from agentscope.rag import KnowledgeBase, SimpleKnowledgeBase
from agentscope.rag import PDFReader, TextReader
from agentscope.embedding import OpenAITextEmbedding
from agentscope.rag import MilvusLiteStore

# 创建知识库
kb = SimpleKnowledgeBase(
    reader=TextReader(chunk_size=512),
    embedding_model=OpenAITextEmbedding(model_name="text-embedding-3-small"),
    embedding_store=MilvusLiteStore(collection_name="docs"),
)

# 添加文档
await kb.add_documents(["这是一个关于机器学习的文档内容"])

# 检索相关文档
docs = await kb.retrieve(query="什么是机器学习？", limit=3)
for doc in docs:
    print(f"分数: {doc.score}, 内容: {doc.metadata.content}")
```

### 4.6.4 RAG 最佳实践

#### 1. Chunk 大小选择

| 使用场景 | 推荐 Chunk Size | 说明 |
|----------|-----------------|------|
| 问答系统 | 256-512 字符 | 精确匹配具体问题 |
| 摘要生成 | 512-1024 字符 | 保留更多上下文 |
| 语义搜索 | 512 字符 | 平衡精确度和召回率 |

#### 2. 混合检索策略

```python showLineNumbers
class HybridKnowledgeBase(KnowledgeBase):
    """结合向量检索和关键词检索"""

    async def retrieve(self, query: str, limit: int = 5, **kwargs):
        # 向量检索
        vector_results = await self.vector_search(query, limit)

        # 关键词检索 (BM25)
        keyword_results = await self.keyword_search(query, limit)

        # RRF 融合
        fused = self.rrf_fusion(vector_results, keyword_results, k=60)
        return fused[:limit]

    def rrf_fusion(self, results1, results2, k=60):
        """倒数排名融合 (RRF)"""
        scores = {}
        for rank, doc in enumerate(results1):
            scores[doc.id] = scores.get(doc.id, 0) + 1 / (k + rank + 1)
        for rank, doc in enumerate(results2):
            scores[doc.id] = scores.get(doc.id, 0) + 1 / (k + rank + 1)
        return sorted(results1 + results2, key=lambda d: scores[d.id], reverse=True)
```

#### 3. 检索结果重排序

可通过增加初始检索数量，再用 LLM 对结果打分排序：

```python
# 简单重排序策略：多检索，取 Top-N
results = await kb.retrieve("我的问题", limit=20)
# 可结合 LLM 对结果进行二次排序
```

#### 4. 增量更新策略

```python showLineNumbers
class IncrementalKnowledgeBase:
    """支持增量更新的知识库"""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.processed_doc_ids = set()

    async def add_if_new(self, file_path: str):
        doc_id = self.kb.reader.get_doc_id(file_path)
        if doc_id not in self.processed_doc_ids:
            docs = await self.kb.reader(file_path)
            await self.kb.add_documents(docs)
            self.processed_doc_ids.add(doc_id)

    async def rebuild_changed(self, file_path: str):
        """重建已修改文档"""
        doc_id = self.kb.reader.get_doc_id(file_path)
        # 删除旧文档
        await self.kb.delete_by_id(doc_id)
        # 重新添加
        docs = await self.kb.reader(file_path)
        await self.kb.add_documents(docs)
```

## 4.7 MsgHub (消息中心)

MsgHub 用于多个 Agent 之间的消息传递和协调。

```python showLineNumbers
from agentscope.pipeline import MsgHub
from agentscope.agent import ReActAgent
from agentscope.tool import Toolkit
from agentscope.formatter import OpenAIChatFormatter

# 创建 Toolkit
toolkit = Toolkit()

# 创建多个 Agent（必须传入 sys_prompt 和 formatter）
agent_a = ReActAgent(name="A", model=..., toolkit=toolkit,
                     sys_prompt="你是 Agent A", formatter=OpenAIChatFormatter())
agent_b = ReActAgent(name="B", model=..., toolkit=toolkit,
                     sys_prompt="你是 Agent B", formatter=OpenAIChatFormatter())
agent_c = ReActAgent(name="C", model=..., toolkit=toolkit,
                     sys_prompt="你是 Agent C", formatter=OpenAIChatFormatter())

# 广播消息模式 (默认)
async with MsgHub(participants=[agent_a, agent_b, agent_c]):
    await agent_a("大家好！")  # B 和 C 都能收到
    await agent_b("收到！")
    await agent_c("我也收到！")

# 关闭自动广播
async with MsgHub(participants=[agent_a, agent_b], enable_auto_broadcast=False):
    result_a = await agent_a("分析一下销售数据")
    result_b = await agent_b(f"基于这个分析写报告: {result_a}")
```

### Java 对比

```java
// Java: JMS / Kafka 消息队列
@Service
public class OrderService {
    private final JmsTemplate jmsTemplate;

    public void processOrder(Order order) {
        // 发送消息到队列
        jmsTemplate.convertAndSend("order.queue", order);
    }
}

// 或者使用 Spring Events
@Component
public class OrderEventListener {
    @EventListener
    public void handleOrderCreated(OrderCreatedEvent event) {
        // 处理事件
    }
}
```

## 4.8 Agent 类型对比与适用场景

### Agent 类型一览

```
┌───────────────────────────────────────────────────────────────────────┐
│                         Agent 继承体系                                  │
├───────────────────────────────────────────────────────────────────────┤
│                                                                        │
│                          AgentBase                                     │
│                        (抽象基类)                                       │
│                    /           \           \                           │
│                   /             \           \                          │
│           ReActAgentBase      UserAgent    A2AAgent                    │
│               /                                            \           │
│              /                                              \          │
│     ReActAgent                                           RealtimeAgent  │
│                                                                        │
└───────────────────────────────────────────────────────────────────────┘
```

| Agent 类型 | 继承关系 | 主要用途 | 关键特性 |
|-----------|---------|---------|----------|
| `ReActAgent` | ReActAgentBase → AgentBase | 通用推理任务 | ReAct循环、工具调用、记忆管理、RAG |
| `UserAgent` | AgentBase | 用户交互 | 支持多种输入源、结构化输入 |
| `A2AAgent` | AgentBase | Agent间通信 | A2A协议、远程Agent调用 |
| `RealtimeAgent` | StateModule | 实时语音交互 | 事件驱动、流式音频处理 |

### 适用场景指南

#### ReActAgent - 通用推理任务
```python showLineNumbers
# 最佳场景：需要工具调用、多轮对话、复杂推理的任务
from agentscope.tool import Toolkit
from agentscope.formatter import OpenAIChatFormatter

toolkit = Toolkit()
toolkit.register_tool_function(tool_func=search_tool, group_name="basic")
toolkit.register_tool_function(tool_func=code_executor, group_name="basic")

agent = ReActAgent(
    name="研究助手",
    sys_prompt="你是一个研究助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,
    memory=InMemoryMemory(),
)

# 支持的高级功能
# - parallel_tool_calls=True: 并行执行多个工具
# - knowledge=[kb1, kb2]: RAG知识库增强
# - plan_notebook=plan_nb: 任务分解规划
# - compression_config: 长对话自动压缩
```

#### UserAgent - 用户输入处理
```python
# 最佳场景：需要人类输入、确认、反馈的任务
user_agent = UserAgent(name="human")

# 支持结构化输入
class UserFeedback(BaseModel):
    rating: int
    comment: str

msg = await user_agent.reply(structured_model=UserFeedback)
# 提示用户输入结构化反馈
```

#### A2AAgent - 跨Agent通信
```python
# 最佳场景：与其他Agent系统互操作
from a2a.types import AgentCard

card = AgentCard(
    name="远程助手",
    url="http://remote-agent:8000/a2a"
)
a2a_agent = A2AAgent(agent_card=card)

# 与远程Agent通信
result = await a2a_agent.reply(Msg("user", "hello", "user"))
```

#### RealtimeAgent - 实时语音交互
```python
# 最佳场景：语音助手、实时对话应用
agent = RealtimeAgent(
    name="Friday",
    sys_prompt="You are a helpful assistant",
    model=DashScopeRealtimeModel(model_name="qwen3-omni-flash-realtime")
)

queue = asyncio.Queue()
await agent.start(queue)

# 处理输出事件
while True:
    event = await queue.get()
    # 处理音频/文本事件
```

## 4.9 最佳实践与常见陷阱

### 最佳实践

#### 1. 合理配置 max_iters
```python showLineNumbers
# 简单任务：减少迭代次数提高响应速度
agent = ReActAgent(..., max_iters=3, sys_prompt="...", formatter=OpenAIChatFormatter())

# 复杂任务：增加迭代次数确保完成
agent = ReActAgent(..., max_iters=15, sys_prompt="...", formatter=OpenAIChatFormatter())
```

#### 2. 使用记忆压缩处理长对话
```python showLineNumbers
compression_config = CompressionConfig(
    enable=True,
    agent_token_counter=token_counter,
    trigger_threshold=4000,
    keep_recent=5,  # 保留最近5条消息对
)
agent = ReActAgent(..., compression_config=compression_config,
                   sys_prompt="...", formatter=OpenAIChatFormatter())
```

#### 3. 并行工具调用加速
```python showLineNumbers
# 多个独立工具调用时启用并行
agent = ReActAgent(
    ...,
    parallel_tool_calls=True,  # 加速独立工具执行
    sys_prompt="...",
    formatter=OpenAIChatFormatter(),
)
```

#### 4. 结构化输出的正确用法
```python showLineNumbers
from pydantic import BaseModel

class TaskResult(BaseModel):
    status: str
    output: str

# 正确：传入 structured_model
result = await agent.reply(msg, structured_model=TaskResult)
# 访问结构化输出
data = result.metadata  # dict 格式的 TaskResult
```

### 常见陷阱

#### 陷阱1: 忘记工具函数返回格式
```python showLineNumbers
# 错误示例：工具函数返回普通 dict（应该返回 ToolResponse 或 str）
def bad_tool() -> dict:
    return {"result": "value"}

# 正确示例：返回 ToolResponse
from agentscope.tool import ToolResponse
from agentscope.message import TextBlock
import json

def good_tool() -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=json.dumps({"result": "value"}))])

# 注册到 Toolkit
toolkit = Toolkit()
toolkit.register_tool_function(tool_func=good_tool, group_name="basic")
```

#### 陷阱2: 阻塞事件循环
```python showLineNumbers
# 错误示例：同步阻塞调用
def sync_search(query):
    time.sleep(10)  # 阻塞整个事件循环！
    return search_sync(query)

# 正确示例：异步实现
async def async_search(query):
    return await asyncio.to_thread(blocking_search, query)
```

#### 陷阱3: 忽视 Token 限制
```python showLineNumbers
# 错误示例：无限制的记忆积累
memory = InMemoryMemory()  # 无压缩，会话过长时LLM调用失败

# 正确示例：配置记忆压缩
agent = ReActAgent(
    ...,
    compression_config=CompressionConfig(
        enable=True,
        agent_token_counter=token_counter,
        trigger_threshold=3000,
    ),
    sys_prompt="...",
    formatter=OpenAIChatFormatter(),
)
```

#### 陷阱4: 结构化输出与工具调用混淆
```python
# 错误：又想用工具又想要结构化输出
result = await agent.reply(
    msg,
    structured_model=SomeModel,
    # tools=[some_tool]  # 不要同时配置！
)

# 正确：结构化输出通过 generate_response 工具实现
# Agent 会自动注册该工具
```

## 4.10 本章小结

本章介绍了 AgentScope 的四大核心概念：

| 概念 | 核心要点 |
|------|---------|
| **Agent** | ReActAgent 通过 ReAct 循环实现推理-行动-观察，创建时必须传入 `sys_prompt`、`formatter` 和 `toolkit` |
| **Model** | 统一的 `ChatModelBase` 抽象层，支持 OpenAI / Anthropic / DashScope / Gemini / Ollama 等多种模型 |
| **Tool** | 通过 `Toolkit` 注册和管理工具函数，自动从函数签名生成 JSON Schema |
| **Memory** | 分为短期记忆（InMemory / Redis / SQLAlchemy）和长期记忆（Mem0 / ReMe），支持 Marks 标签和压缩机制 |

**关键记忆点：**
- ReActAgent 构造函数的必填参数：`name`、`model`、`sys_prompt`、`formatter`
- 工具通过 `Toolkit.register_tool_function()` 注册，不是通过 `tools=[...]` 列表
- Agent 调用必须使用 `await`（异步）
- 记忆压缩通过 `CompressionConfig` 配置

## 练习题

### 练习 4.1: ReAct 推理循环理解 [基础]

**题目**：
请简述 ReActAgent 的推理循环过程，并说明 Reasoning 和 Acting 两个阶段的区别。

**验证方式**：
检查是否正确描述了 ReAct 循环的三个阶段及其作用。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**ReAct 推理循环**：

```
┌─────────────────────────────────────────────────────────────────┐
│                    ReActAgent 推理循环                             │
│                                                                 │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐              │
│  │ Reasoning │ ───▶ │  Acting  │ ───▶ │ Observe  │              │
│  │  (思考)   │      │  (行动)  │      │  (观察)  │              │
│  └────┬─────┘      └────┬─────┘      └────┬─────┘              │
│       │                  │                  │                    │
│       ▼                  ▼                  │                    │
│  ┌──────────┐      ┌──────────┐           │                    │
│  │  LLM     │      │ 执行工具  │           │                    │
│  │  调用    │      │ 或回复    │           │                    │
│  └──────────┘      └──────────┘           │                    │
│                                            │                    │
│       ◀────────────────────────────────────┘                    │
│                      (循环直到完成)                               │
└─────────────────────────────────────────────────────────────────┘
```

** Reasoning（思考）阶段**：
- 调用 LLM，决定下一步动作
- 分析用户意图，决定是否需要调用工具
- 输出：推理结果 + 可能的工具调用指令

** Acting（行动）阶段**：
- 根据 Reasoning 的决定执行工具
- 如果没有工具调用，直接生成回复
- 如果有工具调用，执行工具并获取结果
- 将结果加入记忆，进入下一轮推理

**区别**：
- Reasoning 是"想"，Acting 是"做"
- Reasoning 决定是否需要工具，Acting 执行具体工具
- 两者循环交替，直到 Agent 认为任务完成
</details>

---

### 练习 4.2: 工具注册机制 [中级]

**题目**：
AgentScope 的工具注册机制与常见的装饰器模式（如 LangChain 的 `@tool`）有什么不同？请分析两种方案的优缺点。

**验证方式**：
对比两种工具注册方式的代码结构。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**两种方案的对比**：

| 方面 | AgentScope (Toolkit) | LangChain (@tool 装饰器) |
|------|---------------------|-------------------------|
| 注册方式 | `toolkit.register_tool_function(func)` | `@tool` 装饰器 |
| 函数要求 | 普通 Python 函数 | 需要装饰器标记 |
| 参数解析 | 自动从签名和 docstring 提取 | 自动从类型注解提取 |
| 灵活性 | 高（运行时注册） | 中（编译时绑定） |

**AgentScope 方案优点**：
1. **无侵入性**：不需要修改原函数，适合接入现有代码库
2. **运行时控制**：可以动态添加/删除工具
3. **分组管理**：通过 `group_name` 组织工具组
4. **中间件支持**：支持工具执行前后添加逻辑

**AgentScope 方案缺点**：
1. 注册代码与函数定义分离
2. 需要显式调用注册方法

**代码对比**：

```python showLineNumbers
# AgentScope 方式（注册与定义分离）
def my_tool(input: str) -> ToolResponse:
    """工具描述"""
    return ToolResponse(content=[TextBlock(type="text", text="result")])

toolkit = Toolkit()
toolkit.register_tool_function(my_tool, group_name="basic")

# LangChain 方式（装饰器直接绑定）
@tool
def my_tool(input: str) -> str:
    """工具描述"""
    return "result"
```

**结论**：AgentScope 的方式更适合大型项目和对工具生命周期有精细控制需求的场景。
</details>

---

### 练习 4.3: 记忆系统设计 [中级]

**题目**：
某电商平台需要构建一个客服 Agent，需要记住：
1. 用户的历史订单
2. 用户的偏好（如 shipping_address）
3. 当前对话的上下文

请设计一个记忆方案，说明需要使用哪些记忆组件以及为什么。

**验证方式**：
检查是否正确选择不同类型的记忆组件。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**推荐方案：混合记忆架构**

| 记忆类型 | 存储内容 | 推荐组件 |
|----------|----------|----------|
| 短期记忆 | 当前对话上下文 | `InMemoryMemory` |
| 用户偏好 | 长期保存的偏好信息 | `Mem0LongTermMemory` 或 `ReMePersonalLongTermMemory` |
| 历史订单 | 跨会话的订单数据 | 外部数据库（不通过 AgentScope 记忆系统） |

**代码示例**：

```python showLineNumbers
from agentscope.agent import ReActAgent
from agentscope.memory import InMemoryMemory, Mem0LongTermMemory
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter

# 短期记忆：当前会话
working_memory = InMemoryMemory()

# 长期记忆：用户偏好（使用 Mem0）
long_term_memory = Mem0LongTermMemory(
    agent_name="customer_service",
    user_name="user_123",
    model=OpenAIChatModel(model_name="gpt-4"),
)

agent = ReActAgent(
    name="客服助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个客服助手，记住用户的偏好和历史订单。",
    formatter=OpenAIChatFormatter(),
    memory=working_memory,
    long_term_memory=long_term_memory,
)

# 对于历史订单，应该通过工具访问外部数据库
# 而不是存储在 AgentScope 记忆中
toolkit = Toolkit()
toolkit.register_tool_function(get_order_history, group_name="ecommerce")
toolkit.register_tool_function(update_preference, group_name="ecommerce")
```

**为什么不把所有数据放记忆**：
1. **历史订单数据量大**：不适合放在 LLM 上下文中
2. **需要事务支持**：订单操作需要强一致性
3. **外部系统是 Source of Truth**：数据库才是权威数据源

**记忆系统的正确用法**：
- 短期记忆：当前会话的上下文
- 长期记忆：用户的简单偏好和摘要
- 复杂数据：通过工具访问外部系统
</details>

---

### 练习 4.4: Model 层抽象设计 [挑战]

**题目**：
阅读 AgentScope 的 Model 抽象层设计，分析：
1. 为什么需要 `ChatModelBase` 这个抽象基类？
2. 为什么每个模型需要对应的 Formatter？
3. 如果要新增一个自定义模型（如本地部署的 LLaMA），需要实现哪些核心方法？

**验证方式**：
结合文档中的模型继承体系进行分析。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**1. 为什么需要 ChatModelBase 抽象基类**：

```
┌─────────────────────────────────────────────────────────────┐
│                    ChatModelBase (抽象基类)                     │
├─────────────────────────────────────────────────────────────┤
│  - 统一定义所有模型的接口（__call__ 方法签名）                   │
│  - 统一错误处理和重试逻辑                                      │
│  - 统一工具调用验证（_validate_tool_choice）                    │
│  - 抽象出流式/非流式响应的统一处理                              │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌─────────────────────┼─────────────────────┐
         ▼                     ▼                     ▼
┌─────────────┐       ┌─────────────┐       ┌─────────────┐
│   OpenAI    │       │  Anthropic  │       │   Gemini   │
│  ChatModel  │       │  ChatModel  │       │  ChatModel │
└─────────────┘       └─────────────┘       └─────────────┘
```

**好处**：
- 上层代码（如 ReActAgent）无需关心底层是 OpenAI 还是 Anthropic
- 新增模型只需实现抽象方法，无需修改已有代码（开闭原则）
- 统一添加日志、监控、重试等横切关注点

**2. 为什么需要对应的 Formatter**：

不同模型提供商的 API 消息格式不同：

```python
# OpenAI 格式
{"role": "user", "content": "Hello"}

# Anthropic 格式
{"role": "user", "content": [{"type": "text", "text": "Hello"}]}

# Anthropic 还要求 messages 必须以 assistant 或 user 结尾
```

Formatter 的职责是将 AgentScope 内部的 `Msg` 对象转换为特定模型的格式。

**3. 自定义模型需要实现的核心方法**：

```python
from agentscope.model import ChatModelBase, ChatResponse

class MyLocalModel(ChatModelBase):
    """自定义本地模型"""
    
    def __init__(self, model_name: str, **kwargs):
        super().__init__(**kwargs)
        self.model_name = model_name
    
    async def __call__(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        **kwargs
    ) -> ChatResponse:
        """核心调用方法，必须实现"""
        # 1. 调用本地模型
        # 2. 解析响应
        # 3. 返回 ChatResponse 对象
        ...
    
    def _validate_tool_choice(self, tool_choice: str, tools: list[dict] | None) -> None:
        """验证工具调用参数（可选覆盖）"""
        ...
```

**最小实现清单**：
| 方法 | 必须实现 | 说明 |
|------|----------|------|
| `__call__` | 是 | 核心调用逻辑 |
| `_validate_tool_choice` | 否 | 大多数模型可复用基类实现 |
</details>

---

### 练习 4.5: Hook 机制应用 [中级]

**题目**：
某团队需要在每个 Agent 回复前后记录日志，用于分析 Agent 的响应质量。请使用 AgentScope 的 Hook 机制实现这个功能。

**验证方式**：
检查代码是否正确使用 Hook API。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**Hook 机制实现日志记录**：

```python showLineNumbers
import agentscope
from agentscope.agent import AgentBase, ReActAgent
from agentscope.message import Msg

# 方法1：类级别 Hook（所有 Agent 实例共享）
def log_all_replies(self, kwargs: dict, output: Msg) -> Msg:
    """记录所有 Agent 的回复"""
    print(f"[LOG] Agent {getattr(self, 'name', 'unknown')} 回复: {output.content[:50]}...")
    return output

# 注册类级别 Hook
AgentBase.register_class_hook(
    hook_type="post_reply",
    hook_name="reply_logger",
    hook=log_all_replies,
)

# 方法2：实例级别 Hook（单个 Agent 独有）
def log_sensitive_agent(self, kwargs: dict, output: Msg) -> Msg:
    """记录特定 Agent 的行为"""
    print(f"[AUDIT] Sensitive Agent 输出: {output.content}")
    return output

agent = ReActAgent(
    name="敏感操作助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个敏感操作助手。",
    formatter=OpenAIChatFormatter(),
)

# 注册实例级别 Hook
agent.register_instance_hook(
    hook_type="post_reply",
    hook_name="audit_logger",
    hook=log_sensitive_agent,
)
```

**Hook 类型对照表**：

| Hook 类型 | 触发时机 | 参数 |
|-----------|----------|------|
| `pre_reply` | `reply()` 调用前 | `(self, kwargs)` |
| `post_reply` | `reply()` 调用后 | `(self, kwargs, output)` |
| `pre_observe` | `observe()` 调用前 | `(self, kwargs)` |
| `post_observe` | `observe()` 调用后 | `(self, kwargs, output)` |
| `pre_print` | `print()` 调用前 | `(self, kwargs)` |
| `post_print` | `print()` 调用后 | `(self, kwargs, output)` |

**应用场景扩展**：
1. **性能监控**：记录 `pre_reply` 和 `post_reply` 的时间差
2. **内容审核**：在 `post_reply` 检查输出是否合规
3. **成本控制**：在 `post_reply` 统计 token 使用量
</details>

## 4.11 下一步

- [第五章：架构设计](05_architecture.md) - 深入理解模块设计
- [第六章：开发指南](06_development_guide.md) - 掌握调试和测试
