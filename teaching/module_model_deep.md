# Model 模块与 Token/Embedding 深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [ModelWrapperBase 基类分析](#2-modelwrapperbase-基类分析)
3. [OpenAI 模型适配器分析](#3-openai-模型适配器分析)
4. [DashScope 模型适配器分析](#4-dashscope-模型适配器分析)
5. [Anthropic 模型适配器分析](#5-anthropic-模型适配器分析)
6. [其他模型适配器](#6-其他模型适配器)
7. [Token 计数机制](#7-token-计数机制)
8. [Embedding 模块](#8-embedding-模块)
9. [代码示例](#9-代码示例)
10. [练习题](#10-练习题)

---

## 1. 模块概述

### 1.1 目录结构

```
src/agentscope/model/
├── __init__.py                   # 模块导出
├── _model_base.py               # ChatModelBase 基类
├── _model_response.py           # ChatResponse 响应类
├── _model_usage.py              # ChatUsage 使用统计
├── _openai_model.py             # OpenAI 适配器
├── _anthropic_model.py          # Anthropic 适配器
├── _dashscope_model.py          # 阿里 DashScope 适配器
├── _gemini_model.py             # Google Gemini 适配器
├── _ollama_model.py             # Ollama 本地模型适配器
├── _trinity_model.py            # Trinity 多模型适配器
├── _utils.py                    # 工具函数

src/agentscope/token/
├── __init__.py
├── _token_counter.py           # Token 计数器基类
├── _tiktoken_counter.py         # Tiktoken 实现
└── ...

src/agentscope/embedding/
├── __init__.py
├── _embedding_base.py          # Embedding 模型基类
├── _openai_embedding.py        # OpenAI Embedding
└── ...
```

### 1.2 模型适配器一览

| 模型 | 文件 | 说明 |
|------|------|------|
| OpenAI | `_openai_model.py` | GPT-4、GPT-3.5 等 |
| Anthropic | `_anthropic_model.py` | Claude 系列 |
| DashScope | `_dashscope_model.py` | 阿里通义千问等 |
| Gemini | `_gemini_model.py` | Google Gemini |
| Ollama | `_ollama_model.py` | 本地运行的大模型 |
| Trinity | `_trinity_model.py` | 多模型统一接口 |

---

## 2. ChatModelBase 基类分析

### 2.1 类定义

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_model_base.py:13`

```python
class ChatModelBase:
    """Base class for chat models."""

    model_name: str
    """The model name"""

    stream: bool
    """Is the model output streaming or not"""

    def __init__(
        self,
        model_name: str,
        stream: bool,
    ) -> None:
        """Initialize the chat model base class.

        Args:
            model_name (`str`):
                The name of the model
            stream (`bool`):
                Whether the model output is streaming or not
        """
        self.model_name = model_name
        self.stream = stream
```

### 2.2 抽象方法

**__call__ 方法** (第 38-44 行):

```python
@abstractmethod
async def __call__(
    self,
    *args: Any,
    **kwargs: Any,
) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
    pass
```

### 2.3 工具选择验证

**_validate_tool_choice() 方法** (第 46-77 行):

```python
def _validate_tool_choice(
    self,
    tool_choice: str,
    tools: list[dict] | None,
) -> None:
    """Validate tool_choice parameter."""
    if not isinstance(tool_choice, str):
        raise TypeError(
            f"tool_choice must be str, got {type(tool_choice)}",
        )
    if tool_choice in _TOOL_CHOICE_MODES:
        return

    available_functions = [tool["function"]["name"] for tool in tools]

    if tool_choice not in available_functions:
        all_options = _TOOL_CHOICE_MODES + available_functions
        raise ValueError(
            f"Invalid tool_choice '{tool_choice}'. "
            f"Available options: {', '.join(sorted(all_options))}",
        )
```

---

## 3. OpenAI 模型适配器分析

### 3.1 类定义

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_openai_model.py:71`

```python
class OpenAIChatModel(ChatModelBase):
    """The OpenAI chat model class."""
```

### 3.2 初始化参数

**__init__() 方法** (第 74-173 行):

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model_name` | `str` | - | 模型名称 |
| `api_key` | `str` | None | API 密钥（可从环境变量读取） |
| `stream` | `bool` | True | 是否流式输出 |
| `reasoning_effort` | `str` | None | 推理努力程度（o3/o4 模型） |
| `organization` | `str` | None | OpenAI 组织 ID |
| `stream_tool_parsing` | `bool` | True | 流式工具解析 |
| `client_type` | `str` | "openai" | 客户端类型（openai/azure） |
| `client_kwargs` | `dict` | None | 客户端额外参数 |
| `generate_kwargs` | `dict` | None | 生成额外参数 |

### 3.3 核心调用方法

**__call__() 方法** (第 176-340 行):

```python
@trace_llm
async def __call__(
    self,
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: Literal["auto", "none", "required"] | str | None = None,
    structured_model: Type[BaseModel] | None = None,
    **kwargs: Any,
) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
    """Get the response from OpenAI chat completions API..."""
```

**关键流程**:

1. 消息验证
2. 工具 schema 格式化
3. 结构化输出处理
4. 调用 OpenAI API
5. 解析响应（流式/非流式）

### 3.4 流式响应解析

**_parse_openai_stream_response() 方法** (第 343-556 行):

```python
async def _parse_openai_stream_response(
    self,
    start_datetime: datetime,
    response: AsyncStream,
    structured_model: Type[BaseModel] | None = None,
) -> AsyncGenerator[ChatResponse, None]:
    """Given an OpenAI streaming completion response, extract the content
     blocks and usages from it and yield ChatResponse objects."""
```

**解析内容**:

- 推理内容 (reasoning_content)
- 文本内容 (text)
- 音频内容 (audio)
- 工具调用 (tool_calls)

### 3.5 非流式响应解析

**_parse_openai_completion_response() 方法** (第 558-678 行):

```python
def _parse_openai_completion_response(
    self,
    start_datetime: datetime,
    response: ChatCompletion,
    structured_model: Type[BaseModel] | None = None,
) -> ChatResponse:
    """Given an OpenAI chat completion response object, extract the content
        blocks and usages from it."""
```

---

## 4. DashScope 模型适配器分析

### 4.1 类定义

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_dashscope_model.py:51`

```python
class DashScopeChatModel(ChatModelBase):
    """The DashScope chat model class, which unifies the Generation and
    MultimodalConversation APIs into one method.

    This class provides a unified interface for DashScope API by automatically
    selecting between text-only (Generation API) and multimodal
    (MultiModalConversation API) endpoints.
    """
```

### 4.2 多模态自动选择

**关键特性** (第 51-71 行):

```python
"""
- When `multimodality=True`: Forces use of MultiModalConversation API
- When `multimodality=False`: Forces use of Generation API for text-only
- When `multimodality=None` (default): Automatically selects the API based on
  model name (e.g., models with "-vl" suffix or starting with "qvq")
"""
```

### 4.3 初始化参数

**__init__() 方法** (第 73-161 行):

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model_name` | `str` | - | 模型名称 |
| `api_key` | `str` | - | DashScope API 密钥 |
| `stream` | `bool` | True | 是否流式输出 |
| `enable_thinking` | `bool` | None | 启用思考（Qwen3、QwQ、DeepSeek-R1） |
| `multimodality` | `bool` | None | 强制多模态模式 |
| `generate_kwargs` | `dict` | None | 生成额外参数 |
| `base_http_api_url` | `str` | None | 自定义 API URL |
| `stream_tool_parsing` | `bool` | True | 流式工具解析 |

### 4.4 API 选择逻辑

**__call__() 方法中的 API 选择** (第 264-283 行):

```python
if self.multimodality or (
    self.multimodality is None
    and (
        self.model_name.startswith("qvq")
        or "-vl" in self.model_name
    )
):
    # 使用多模态对话 API
    response = await dashscope.AioMultiModalConversation.call(
        api_key=self.api_key,
        **kwargs,
    )
else:
    # 使用文本生成 API
    response = await dashscope.aigc.generation.AioGeneration.call(
        api_key=self.api_key,
        **kwargs,
    )
```

### 4.5 流式响应解析

**_parse_dashscope_stream_response() 方法** (第 300-486 行):

```python
async def _parse_dashscope_stream_response(
    self,
    start_datetime: datetime,
    response: Union[
        AsyncGenerator[GenerationResponse, None],
        AsyncGenerator[MultiModalConversationResponse, None],
        Generator[MultiModalConversationResponse, None, None],
    ],
    structured_model: Type[BaseModel] | None = None,
) -> AsyncGenerator[ChatResponse, Any]:
```

**关键特性**:

- 支持增量输出解析 (`incremental_output=True`)
- 累积工具调用参数
- 流式 JSON 解析和修复

---

## 5. Anthropic 模型适配器分析

### 5.1 主要特性

- Claude 3.5 Sonnet、Claude 3 Opus 等支持
- 原生工具调用支持
- 消息流式处理
- 结构化输出支持

### 5.2 API 差异

Anthropic 的 API 与 OpenAI 有一些关键差异:

1. **消息格式**: 使用 `messages` 而非 `chat completions`
2. **工具调用**: 原生 `tools` 和 `tool_choice` 参数
3. **Thinking**: Claude 3.5+ 支持扩展思考

---

## 6. 其他模型适配器

### 6.1 Gemini 模型

**文件**: `_gemini_model.py`

- Google Gemini Pro/Flash 支持
- 多模态输入支持
- Vertex AI 和 Gemini API 两种模式

### 6.2 Ollama 模型

**文件**: `_ollama_model.py`

- 本地大模型运行
- 支持 Llama 2、Mistral 等开源模型
- REST API 通信

### 6.3 Trinity 模型

**文件**: `_trinity_model.py`

- 多模型统一接口
- 模型路由和负载均衡
- 故障转移支持

---

## 7. Token 计数机制

### 7.1 Token 计数器基类

```python
class TokenCounterBase:
    """Base class for token counters."""

    @abstractmethod
    async def count(self, text: str | list[dict]) -> int:
        """Count the number of tokens in the given text."""
        pass
```

### 7.2 Tiktoken 实现

```python
class TiktokenTokenCounter(TokenCounterBase):
    """Token counter using OpenAI's tiktoken library."""

    def __init__(self, model_name: str = "cl100k_base"):
        self.model_name = model_name
        self._encoder = tiktoken.get_encoding(model_name)

    async def count(self, text: str | list[dict]) -> int:
        if isinstance(text, str):
            return len(self._encoder.encode(text))
        else:
            # Handle message format
            total = 0
            for msg in text:
                total += len(self._encoder.encode(msg.get("content", "")))
            return total
```

### 7.3 使用示例

```python
from agentscope.token import TiktokenTokenCounter

counter = TiktokenTokenCounter(model_name="cl100k_base")
count = await counter.count("Hello, world!")
print(f"Token count: {count}")
```

---

## 8. Embedding 模块

### 8.1 Embedding 模型基类

```python
class EmbeddingModelBase:
    """Base class for embedding models."""

    model_name: str

    @abstractmethod
    async def __call__(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """Get embeddings for the given texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors.
        """
        pass
```

### 8.2 OpenAI Embedding

```python
class OpenAIEmbeddingModel(EmbeddingModelBase):
    """OpenAI embedding model implementation."""

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: str = None,
        **kwargs,
    ):
        super().__init__(model_name)
        self.client = OpenAI(api_key=api_key, **kwargs)

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(
            model=self.model_name,
            input=texts,
        )
        return [item.embedding for item in response.data]
```

---

## 9. 代码示例

### 9.1 使用 OpenAI 模型

```python
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIFormatter

# 创建模型实例
model = OpenAIChatModel(
    model_name="gpt-4",
    api_key="your-api-key",
    stream=True,
)

# 创建格式化器
formatter = OpenAIFormatter()

# 准备消息
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is the capital of France?"},
]

# 同步调用
response = await model(messages)
print(f"Response: {response.content}")
```

### 9.2 流式输出处理

```python
async def stream_chat():
    model = OpenAIChatModel(model_name="gpt-4", stream=True)

    messages = [
        {"role": "user", "content": "Write a story about a robot."},
    ]

    # 流式处理响应
    async for chunk in await model(messages):
        for block in chunk.content:
            if block["type"] == "text":
                print(block["text"], end="", flush=True)
```

### 9.3 工具调用

```python
from agentscope.model import OpenAIChatModel

model = OpenAIChatModel(model_name="gpt-4")

# 定义工具
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city name",
                    },
                },
                "required": ["location"],
            },
        },
    }
]

messages = [
    {"role": "user", "content": "What's the weather in Paris?"},
]

# 调用模型，强制使用工具
response = await model(
    messages,
    tools=tools,
    tool_choice="required",
)

# 处理工具调用
for block in response.content:
    if block["type"] == "tool_use":
        print(f"Tool: {block['name']}")
        print(f"Args: {block['input']}")
```

### 9.4 结构化输出

```python
from pydantic import BaseModel
from typing import Literal

class WeatherResponse(BaseModel):
    location: str
    temperature: float
    unit: Literal["celsius", "fahrenheit"]
    condition: str

model = OpenAIChatModel(model_name="gpt-4")

messages = [
    {"role": "user", "content": "What's the weather like in Tokyo?"},
]

# 请求结构化输出
response = await model(
    messages,
    structured_model=WeatherResponse,
)

# 访问结构化结果
if response.metadata:
    weather = WeatherResponse(**response.metadata)
    print(f"{weather.location}: {weather.temperature}{weather.unit}")
```

### 9.5 DashScope 多模态模型

```python
from agentscope.model import DashScopeChatModel

# 强制使用多模态模式
model = DashScopeChatModel(
    model_name="qwen-vl-max",
    api_key="your-api-key",
    multimodality=True,  # 强制使用多模态 API
)

# 发送多模态消息
messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "What is in this image?"},
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/image.jpg"},
            },
        ],
    }
]

response = await model(messages)
```

---

## 10. 练习题

### 10.1 基础题

1. **分析 `ChatModelBase` 基类的设计意图，参考 `_model_base.py:13-77`。**

2. **OpenAI 和 DashScope 模型适配器在处理流式输出时有何异同？**

3. **解释 `tool_choice` 参数的作用，并说明其可选值。**

### 10.2 进阶题

4. **分析结构化输出的实现机制，以 OpenAI 适配器为例。**

5. **设计一个模型适配器，支持同时调用多个模型并合并结果。**

6. **分析 DashScope 适配器如何自动选择 Generation 和 MultimodalConversation API。**

### 10.3 挑战题

7. **实现一个自定义 Token 计数器，支持滑动窗口截断。**

8. **分析 AgentScope 的模型适配器与 LangChain 的 Model IO 有何异同。**

9. **设计一个模型代理，实现请求重试、负载均衡和故障转移。**

---

## 参考资料

- 源码路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/`
- Token 模块: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/token/`
- Embedding 模块: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/embedding/`

---

*文档版本: 1.0*
*最后更新: 2026-04-27*
