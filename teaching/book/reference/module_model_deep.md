# Model 模块与 Token/Embedding 深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [ChatModelBase 基类分析](#2-chatmodelbase-基类分析)
3. [OpenAI 模型适配器分析](#3-openai-模型适配器分析)
4. [DashScope 模型适配器分析](#4-dashscope-模型适配器分析)
5. [Anthropic 模型适配器分析](#5-anthropic-模型适配器分析)
6. [其他模型适配器](#6-其他模型适配器)
7. [工具调用机制详解](#7-工具调用机制详解)
8. [Token 计数机制](#8-token-计数机制)
9. [Embedding 模块](#9-embedding-模块)
10. [代码示例](#10-代码示例)
11. [练习题](#11-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 AgentScope 支持的主要模型适配器及其对应文件位置 | 列举、识别 |
| 理解 | 解释 `ChatModelBase` 抽象层的设计意图，以及各适配器如何处理差异化的 API 格式 | 解释、比较 |
| 应用 | 配置并使用至少两种模型适配器（如 OpenAI 和 DashScope）完成 LLM 调用 | 配置、使用 |
| 分析 | 分析工具调用机制中 `tool_choice`、`structured_model` 和常规调用的优先级与互斥关系 | 分析、诊断 |
| 评价 | 评价不同 Token 计数策略在成本控制与精度之间的权衡 | 评价、比较 |
| 创造 | 基于 `ChatModelBase` 设计并接入一个新的自定义模型适配器 | 设计、构建 |

## 先修检查

在开始学习本模块之前，请确认您已掌握以下知识：

- [ ] Python 异步编程基础 (`async`/`await`)
- [ ] 类型注解和泛型基础 (`TypeVar`、`Generic`)
- [ ] Pydantic `BaseModel` 的基本用法
- [ ] 至少一种 LLM API（如 OpenAI API）的基本调用方式

**预计学习时间**: 40 分钟

### Java 开发者对照

| Python 概念 | Java 等价物 | 说明 |
|-------------|------------|------|
| `async def __call__` | `CompletableFuture<ChatResponse> call()` | 可调用对象 ≈ Function 接口 |
| `@trace_llm` | AOP `@Around` 切面 | 装饰器 ≈ 拦截器 |
| Pydantic `BaseModel` | Bean Validation (`@Valid`) | 数据验证框架 |
| `yield` 生成器 | `Stream.Builder` / `Flux` | 流式数据处理 |
| `tool_choice` | Strategy 模式 | 运行时选择行为 |

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

src/agentscope/token/
├── __init__.py
├── _token_base.py               # TokenCounterBase 基类
├── _openai_token_counter.py     # OpenAI Token 计数器
├── _anthropic_token_counter.py  # Anthropic Token 计数器
├── _char_token_counter.py       # 字符 Token 计数器
├── _gemini_token_counter.py     # Gemini Token 计数器
└── _huggingface_token_counter.py # HuggingFace Token 计数器

src/agentscope/embedding/
├── __init__.py
├── _embedding_base.py          # EmbeddingModelBase 基类
├── _embedding_response.py       # EmbeddingResponse 响应类
├── _embedding_usage.py         # EmbeddingUsage 使用统计
├── _openai_embedding.py        # OpenAI Embedding
├── _dashscope_embedding.py     # DashScope Embedding
├── _gemini_embedding.py        # Gemini Embedding
├── _ollama_embedding.py        # Ollama Embedding
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

> **注**: 本文档中的源码行号为参考值，不同版本可能有所变动，建议以方法名定位。

### 2.1 完整源码

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_model_base.py`

```python
# -*- coding: utf-8 -*-
"""The chat model base class."""

from abc import abstractmethod
from typing import AsyncGenerator, Any

from ._model_response import ChatResponse


_TOOL_CHOICE_MODES = ["auto", "none", "required"]


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

    @abstractmethod
    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        pass

    def _validate_tool_choice(
        self,
        tool_choice: str,
        tools: list[dict] | None,
    ) -> None:
        """Validate tool_choice parameter.

        Args:
            tool_choice (`str`):
                Tool choice mode or function name
            tools (`list[dict] | None`):
                Available tools list
        Raises:
            TypeError: If tool_choice is not string
            ValueError: If tool_choice is invalid
        """
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

### 2.2 源码解析

`★ Insight ─────────────────────────────────────`
- **抽象基类设计模式**: `ChatModelBase` 使用抽象基类模式，定义统一接口约束所有模型适配器
- **异步设计**: 所有模型调用都是异步的 (`async def`)，支持高并发场景
- **工具验证逻辑**: `_validate_tool_choice` 实现了双重验证：既支持标准模式 (auto/none/required)，也支持特定工具名
`─────────────────────────────────────────────────`

**核心设计要点**:

| 元素 | 说明 |
|------|------|
| `model_name` | 类属性，标识具体模型 |
| `stream` | 类属性，控制是否流式输出 |
| `__call__` | 抽象方法，子类必须实现模型调用逻辑 |
| `_TOOL_CHOICE_MODES` | 模块级常量，定义标准工具选择模式 |

### 2.3 模型调用流程图

```
User Code
    │
    ▼
model(messages, tools, tool_choice, ...)
    │
    ▼
┌─────────────────────────────────────────┐
│  1. 消息验证 (validate messages)          │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  2. 工具格式化 (format tools)            │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  3. 参数组装 (build API kwargs)          │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  4. API 调用 (call provider API)         │
│     - stream=True → AsyncStream          │
│     - stream=False → ChatCompletion      │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  5. 响应解析 (parse response)            │
│     - 流式: _parse_stream_response()     │
│     - 非流式: _parse_completion_response()│
└─────────────────────────────────────────┘
    │
    ▼
ChatResponse | AsyncGenerator[ChatResponse]
```

### 2.4 ChatResponse 数据类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_model_response.py` (第 1-42 行)

```python
@dataclass
class ChatResponse(DictMixin):
    """The response of chat models."""

    content: Sequence[TextBlock | ToolUseBlock | ThinkingBlock | AudioBlock]
    """The content of the chat response, which can include text blocks,
    tool use blocks, or thinking blocks."""

    id: str = field(default_factory=lambda: _get_timestamp(True))
    """The unique identifier formatter """

    created_at: str = field(default_factory=_get_timestamp)
    """When the response was created"""

    type: Literal["chat"] = field(default_factory=lambda: "chat")
    """The type of the response, which is always 'chat'."""

    usage: ChatUsage | None = field(default_factory=lambda: None)
    """The usage information of the chat response, if available."""

    metadata: dict[str, JSONSerializableObject] | None = field(
        default_factory=lambda: None,
    )
    """The metadata of the chat response"""
```

### 2.5 ChatUsage 数据类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_model_usage.py` (第 1-26 行)

```python
@dataclass
class ChatUsage(DictMixin):
    """The usage of a chat model API invocation."""

    input_tokens: int
    """The number of input tokens."""

    output_tokens: int
    """The number of output tokens."""

    time: float
    """The time used in seconds."""

    type: Literal["chat"] = field(default_factory=lambda: "chat")
    """The type of the usage, must be `chat`."""

    metadata: dict[str, Any] | None = field(default_factory=lambda: None)
    """The metadata of the usage."""
```

---

## 3. OpenAI 模型适配器分析

### 3.1 完整源码

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_openai_model.py`

#### 3.1.1 音频数据格式化 (第 46-68 行)

```python
def _format_audio_data_for_qwen_omni(messages: list[dict]) -> None:
    """Qwen-omni uses OpenAI-compatible API but requires different audio
    data format than OpenAI with "data:;base64," prefix.
    Refer to `Qwen-omni documentation` for more details.

    Args:
        messages (`list[dict]`):
            The list of message dictionaries from OpenAI formatter.
    """
    for msg in messages:
        if isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if (
                    isinstance(block, dict)
                    and "input_audio" in block
                    and isinstance(block["input_audio"].get("data"), str)
                ):
                    if not block["input_audio"]["data"].startswith("http"):
                        block["input_audio"]["data"] = (
                            "data:;base64," + block["input_audio"]["data"]
                        )
```

#### 3.1.2 OpenAIChatModel 类初始化 (第 71-175 行)

```python
class OpenAIChatModel(ChatModelBase):
    """The OpenAI chat model class."""

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        stream: bool = True,
        reasoning_effort: Literal["low", "medium", "high"] | None = None,
        organization: str = None,
        stream_tool_parsing: bool = True,
        client_type: Literal["openai", "azure"] = "openai",
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        **kwargs: Any,
    ) -> None:
        # Handle deprecated client_args parameter from kwargs
        client_args = kwargs.pop("client_args", None)
        if client_args is not None and client_kwargs is not None:
            raise ValueError(
                "Cannot specify both 'client_args' and 'client_kwargs'. "
                "Please use only 'client_kwargs' (client_args is deprecated).",
            )

        if client_args is not None:
            logger.warning(
                "The parameter 'client_args' is deprecated and will be "
                "removed in a future version. Please use 'client_kwargs' "
                "instead. Automatically converting 'client_args' to "
                "'client_kwargs'.",
            )
            client_kwargs = client_args

        if kwargs:
            logger.warning(
                "Unknown keyword arguments: %s. These will be ignored.",
                list(kwargs.keys()),
            )

        super().__init__(model_name, stream)

        import openai

        if client_type not in ("openai", "azure"):
            raise ValueError(
                "Invalid client_type. Supported values: 'openai', 'azure'.",
            )

        if client_type == "azure":
            self.client = openai.AsyncAzureOpenAI(
                api_key=api_key,
                organization=organization,
                **(client_kwargs or {}),
            )
        else:
            self.client = openai.AsyncClient(
                api_key=api_key,
                organization=organization,
                **(client_kwargs or {}),
            )

        self.reasoning_effort = reasoning_effort
        self.stream_tool_parsing = stream_tool_parsing
        self.generate_kwargs = generate_kwargs or {}
        self._structured_output_fallback = False
```

#### 3.1.3 核心调用方法 __call__ (第 176-342 行)

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
    # checking messages
    if not isinstance(messages, list):
        raise ValueError(
            "OpenAI `messages` field expected type `list`, "
            f"got `{type(messages)}` instead.",
        )
    if not all("role" in msg and "content" in msg for msg in messages):
        raise ValueError(
            "Each message in the 'messages' list must contain a 'role' "
            "and 'content' key for OpenAI API.",
        )

    # Qwen-omni requires different base64 audio format from openai
    if "omni" in self.model_name.lower():
        _format_audio_data_for_qwen_omni(messages)

    kwargs = {
        "model": self.model_name,
        "messages": messages,
        "stream": self.stream,
        **self.generate_kwargs,
        **kwargs,
    }
    if self.reasoning_effort and "reasoning_effort" not in kwargs:
        kwargs["reasoning_effort"] = self.reasoning_effort

    if tools:
        kwargs["tools"] = self._format_tools_json_schemas(tools)

    if tool_choice:
        # Handle deprecated "any" option with warning
        if tool_choice == "any":
            warnings.warn(
                '"any" is deprecated and will be removed in a future '
                "version.",
                DeprecationWarning,
            )
            tool_choice = "required"
        self._validate_tool_choice(tool_choice, tools)
        kwargs["tool_choice"] = self._format_tool_choice(tool_choice)

    if self.stream:
        kwargs["stream_options"] = {"include_usage": True}

    start_datetime = datetime.now()

    if structured_model:
        if tools or tool_choice:
            logger.warning(
                "structured_model is provided. Both 'tools' and "
                "'tool_choice' parameters will be overridden and "
                "ignored. The model will only perform structured output "
                "generation without calling any other tools.",
            )
        kwargs.pop("stream", None)
        kwargs.pop("tools", None)
        kwargs.pop("tool_choice", None)

        if self._structured_output_fallback:
            response = await self._structured_via_tool_call(
                kwargs,
                structured_model,
                start_datetime,
            )
            if isinstance(response, AsyncGenerator):
                return response
        else:
            kwargs["response_format"] = structured_model
            try:
                if not self.stream:
                    response = await self.client.chat.completions.parse(
                        **kwargs,
                    )
                else:
                    response = self.client.chat.completions.stream(
                        **kwargs,
                    )
                    return self._parse_openai_stream_response(
                        start_datetime,
                        response,
                        structured_model,
                    )
            except Exception as e:
                logger.warning(
                    "response_format structured output failed (%s: %s), "
                    "falling back to tool-call based structured output. "
                    "Subsequent calls will use tool-call directly.",
                    type(e).__name__,
                    e,
                )
                self._structured_output_fallback = True
                response = await self._structured_via_tool_call(
                    kwargs,
                    structured_model,
                    start_datetime,
                )
                if isinstance(response, AsyncGenerator):
                    return response
    else:
        response = await self.client.chat.completions.create(**kwargs)

    if self.stream:
        return self._parse_openai_stream_response(
            start_datetime,
            response,
            structured_model,
        )

    # Non-streaming response
    parsed_response = self._parse_openai_completion_response(
        start_datetime,
        response,
        structured_model,
    )

    return parsed_response
```

`★ Insight ─────────────────────────────────────`
- **双重结构化输出策略**: OpenAI 适配器先尝试原生的 `response_format`，失败后回退到工具调用方式
- **流式工具解析**: `stream_tool_parsing=True` 时增量解析 JSON，避免等待完整响应
- **Azure 兼容性**: 通过 `client_type` 参数支持 Azure OpenAI
- **废弃参数处理**: `client_args` 被标记为废弃，自动转换为 `client_kwargs`
`─────────────────────────────────────────────────`

### 3.2 流式响应解析详解

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
    usage, res = None, None
    response_id: str | None = None
    text = ""
    thinking = ""
    audio = ""
    tool_calls = OrderedDict()
    last_input_objs = {}  # Store last input_obj for each tool_call
    metadata: dict | None = None
    contents: List[TextBlock | ToolUseBlock | ThinkingBlock | AudioBlock] = []
    last_contents = None

    async with response as stream:
        async for item in stream:
            if structured_model and not self._structured_output_fallback:
                if item.type != "chunk":
                    continue
                chunk = item.chunk
            else:
                chunk = item

            if response_id is None:
                response_id = getattr(chunk, "id", None)

            if chunk.usage:
                usage = ChatUsage(
                    input_tokens=chunk.usage.prompt_tokens,
                    output_tokens=chunk.usage.completion_tokens,
                    time=(datetime.now() - start_datetime).total_seconds(),
                    metadata=chunk.usage,
                )

            if not chunk.choices:
                if usage and contents:
                    _kwargs = {
                        "content": contents,
                        "usage": usage,
                        "metadata": metadata,
                    }
                    if response_id:
                        _kwargs["id"] = response_id
                    res = ChatResponse(**_kwargs)
                    yield res
                continue

            choice = chunk.choices[0]

            # 提取推理内容
            delta_reasoning = getattr(choice.delta, "reasoning_content", None)
            if not isinstance(delta_reasoning, str):
                delta_reasoning = getattr(choice.delta, "reasoning", None)
            if not isinstance(delta_reasoning, str):
                delta_reasoning = ""

            thinking += delta_reasoning
            text += getattr(choice.delta, "content", None) or ""

            # 提取音频内容
            if hasattr(choice.delta, "audio") and "data" in choice.delta.audio:
                audio += choice.delta.audio["data"]
            if hasattr(choice.delta, "audio") and "transcript" in choice.delta.audio:
                text += choice.delta.audio["transcript"]

            # 提取工具调用
            for tool_call in getattr(choice.delta, "tool_calls", None) or []:
                if tool_call.index in tool_calls:
                    if tool_call.function.arguments is not None:
                        tool_calls[tool_call.index]["input"] += tool_call.function.arguments
                else:
                    tool_calls[tool_call.index] = {
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "input": tool_call.function.arguments or "",
                    }

            contents = []

            if thinking:
                contents.append(ThinkingBlock(type="thinking", thinking=thinking))

            if audio:
                media_type = self.generate_kwargs.get("audio", {}).get("format", "wav")
                contents.append(AudioBlock(
                    type="audio",
                    source=Base64Source(
                        data=audio,
                        media_type=f"audio/{media_type}",
                        type="base64",
                    ),
                ))

            if text:
                contents.append(TextBlock(type="text", text=text))
                if structured_model:
                    metadata = _json_loads_with_repair(text)

            # 处理工具调用
            for tool_call in tool_calls.values():
                input_str = tool_call["input"]
                tool_id = tool_call["id"]

                if self.stream_tool_parsing:
                    repaired_input = _parse_streaming_json_dict(
                        input_str,
                        last_input_objs.get(tool_id),
                    )
                    last_input_objs[tool_id] = repaired_input
                else:
                    repaired_input = {}

                contents.append(
                    ToolUseBlock(
                        type=tool_call["type"],
                        id=tool_id,
                        name=tool_call["name"],
                        input=repaired_input,
                        raw_input=input_str,
                    ),
                )

            if contents:
                _kwargs = {
                    "content": contents,
                    "usage": usage,
                    "metadata": metadata,
                }
                if response_id:
                    _kwargs["id"] = response_id
                res = ChatResponse(**_kwargs)
                yield res
                last_contents = copy.deepcopy(contents)

    # 处理最后的工具调用
    if not self.stream_tool_parsing and tool_calls and last_contents:
        metadata = None
        for block in last_contents:
            if block.get("type") == "tool_use":
                block["input"] = _json_loads_with_repair(
                    str(block.get("raw_input") or "{}"),
                )
                if structured_model:
                    metadata = block["input"]

        _kwargs = {
            "content": last_contents,
            "usage": usage,
            "metadata": metadata,
        }
        if response_id:
            _kwargs["id"] = response_id
        yield ChatResponse(**_kwargs)
```

### 3.3 非流式响应解析

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
    content_blocks: List[TextBlock | ToolUseBlock | ThinkingBlock | AudioBlock] = []
    metadata: dict | None = None

    if response.choices:
        choice = response.choices[0]

        # 提取推理内容
        reasoning = getattr(choice.message, "reasoning_content", None)
        if not isinstance(reasoning, str):
            reasoning = getattr(choice.message, "reasoning", None)
        if not isinstance(reasoning, str):
            reasoning = None

        if reasoning is not None:
            content_blocks.append(ThinkingBlock(type="thinking", thinking=reasoning))

        # 提取文本内容
        if choice.message.content:
            content_blocks.append(TextBlock(
                type="text",
                text=response.choices[0].message.content,
            ))

        # 提取音频内容
        if choice.message.audio:
            media_type = self.generate_kwargs.get("audio", {}).get("format", "mp3")
            content_blocks.append(AudioBlock(
                type="audio",
                source=Base64Source(
                    data=choice.message.audio.data,
                    media_type=f"audio/{media_type}",
                    type="base64",
                ),
            ))
            if choice.message.audio.transcript:
                content_blocks.append(TextBlock(
                    type="text",
                    text=choice.message.audio.transcript,
                ))

        # 提取工具调用
        for tool_call in choice.message.tool_calls or []:
            content_blocks.append(
                ToolUseBlock(
                    type="tool_use",
                    id=tool_call.id,
                    name=tool_call.function.name,
                    input=_json_loads_with_repair(tool_call.function.arguments),
                ),
            )

        # 提取结构化输出
        if structured_model:
            try:
                parsed = choice.message.parsed
            except AttributeError:
                parsed = None
            if parsed is not None:
                metadata = parsed.model_dump()
            elif choice.message.tool_calls:
                metadata = _json_loads_with_repair(
                    choice.message.tool_calls[0].function.arguments,
                )

    # 提取 usage 信息
    usage = None
    if response.usage:
        usage = ChatUsage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            time=(datetime.now() - start_datetime).total_seconds(),
            metadata=response.usage,
        )

    resp_kwargs = {
        "content": content_blocks,
        "usage": usage,
        "metadata": metadata,
    }
    response_id = getattr(response, "id", None)
    if response_id:
        resp_kwargs["id"] = response_id

    return ChatResponse(**resp_kwargs)
```

### 3.4 结构化输出回退机制

**_structured_via_tool_call() 方法** (第 680-707 行):

```python
async def _structured_via_tool_call(
    self,
    kwargs: dict,
    structured_model: Type[BaseModel],
    start_datetime: datetime,
) -> Any:
    """Use tool-call approach for structured output.

    Falls back to this when the API endpoint does not support
    json_schema response_format (e.g. DashScope, DeepSeek).
    """
    kwargs.pop("response_format", None)
    format_tool = _create_tool_from_base_model(structured_model)
    kwargs["tools"] = self._format_tools_json_schemas([format_tool])
    kwargs["tool_choice"] = self._format_tool_choice(
        format_tool["function"]["name"],
    )
    if self.stream:
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
    response = await self.client.chat.completions.create(**kwargs)
    if self.stream:
        return self._parse_openai_stream_response(
            start_datetime,
            response,
            structured_model,
        )
    return response
```

### 3.5 OpenAI vs DashScope 关键差异

| 特性 | OpenAI | DashScope |
|------|--------|-----------|
| 流式工具解析 | `stream_tool_parsing` | `stream_tool_parsing` |
| 结构化输出 | `response_format` 原生支持 | 仅通过工具调用实现 |
| 推理内容 | `reasoning_content` | `reasoning_content` |
| 音频支持 | `audio` 参数 | 不支持 |
| 工具选择 | `auto/none/required/函数名` | `auto/none` (required 转为 auto) |
| Azure 支持 | `client_type="azure"` | 不支持 |

---

## 4. DashScope 模型适配器分析

### 4.1 完整源码

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_dashscope_model.py`

#### 4.1.1 DashScopeChatModel 类初始化 (第 51-162 行)

```python
class DashScopeChatModel(ChatModelBase):
    """The DashScope chat model class, which unifies the Generation and
    MultimodalConversation APIs into one method.

    This class provides a unified interface for DashScope API by automatically
    selecting between text-only (Generation API) and multimodal
    (MultiModalConversation API) endpoints. The `multimodality` parameter
    allows explicit control over API selection:

    - When `multimodality=True`: Forces use of MultiModalConversation API
      for handling images, videos, and other multimodal inputs
    - When `multimodality=False`: Forces use of Generation API for
      text-only processing
    - When `multimodality=None` (default): Automatically selects the API
      based on model name (e.g., models with "-vl" suffix or starting
      with "qvq" will use MultiModalConversation API)
    """

    def __init__(
        self,
        model_name: str,
        api_key: str,
        stream: bool = True,
        enable_thinking: bool | None = None,
        multimodality: bool | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        base_http_api_url: str | None = None,
        stream_tool_parsing: bool = True,
        **_kwargs: Any,
    ) -> None:
        if enable_thinking and not stream:
            logger.info(
                "In DashScope API, `stream` must be True when "
                "`enable_thinking` is True. ",
            )
            stream = True

        super().__init__(model_name, stream)

        self.api_key = api_key
        self.enable_thinking = enable_thinking
        self.multimodality = multimodality
        self.generate_kwargs = generate_kwargs or {}
        self.stream_tool_parsing = stream_tool_parsing

        if base_http_api_url is not None:
            import dashscope
            dashscope.base_http_api_url = base_http_api_url

        # Load headers from environment variable if exists
        headers = os.getenv("DASHSCOPE_API_HEADERS")
        if headers:
            try:
                headers = json.loads(str(headers))
                if not isinstance(headers, dict):
                    raise json.JSONDecodeError("", "", 0)

                if self.generate_kwargs.get("headers"):
                    headers.update(self.generate_kwargs["headers"])

                self.generate_kwargs["headers"] = headers

            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse DASHSCOPE_API_HEADERS environment "
                    "variable as JSON. It should be a JSON object.",
                )
```

#### 4.1.2 核心调用方法 __call__ (第 163-300 行)

```python
@trace_llm
async def __call__(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    tool_choice: Literal["auto", "none", "required"] | str | None = None,
    structured_model: Type[BaseModel] | None = None,
    **kwargs: Any,
) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
    import dashscope

    kwargs = {
        "messages": messages,
        "model": self.model_name,
        "stream": self.stream,
        "result_format": "message",
        # In agentscope, the `incremental_output` must be `True` when
        # `self.stream` is True
        "incremental_output": self.stream,
        **self.generate_kwargs,
        **kwargs,
    }

    if tools:
        kwargs["tools"] = self._format_tools_json_schemas(tools)

    if tool_choice:
        if tool_choice in ["any", "required"]:
            warnings.warn(
                f"'{tool_choice}' is not supported by DashScope API. "
                "It will be converted to 'auto'.",
                DeprecationWarning,
            )
            tool_choice = "auto"

        self._validate_tool_choice(tool_choice, tools)
        kwargs["tool_choice"] = self._format_tool_choice(tool_choice)

    if self.enable_thinking is not None and "enable_thinking" not in kwargs:
        kwargs["enable_thinking"] = self.enable_thinking

    if structured_model:
        if tools or tool_choice:
            logger.warning(
                "structured_model is provided. Both 'tools' and "
                "'tool_choice' parameters will be overridden and "
                "ignored. The model will only perform structured output "
                "generation without calling any other tools.",
            )
        format_tool = _create_tool_from_base_model(structured_model)
        kwargs["tools"] = self._format_tools_json_schemas([format_tool])
        kwargs["tool_choice"] = self._format_tool_choice(
            format_tool["function"]["name"],
        )

    start_datetime = datetime.now()

    # 根据模型名称自动选择 API
    if self.multimodality or (
        self.multimodality is None
        and (
            self.model_name.startswith("qvq")
            or "-vl" in self.model_name
        )
    ):
        response = await dashscope.AioMultiModalConversation.call(
            api_key=self.api_key,
            **kwargs,
        )
    else:
        response = await dashscope.aigc.generation.AioGeneration.call(
            api_key=self.api_key,
            **kwargs,
        )

    if self.stream:
        return self._parse_dashscope_stream_response(
            start_datetime,
            response,
            structured_model,
        )

    parsed_response = await self._parse_dashscope_generation_response(
        start_datetime,
        response,
        structured_model,
    )

    return parsed_response
```

`★ Insight ─────────────────────────────────────`
- **API 自动选择**: DashScope 适配器根据模型名称自动选择 API——包含 "-vl" 或 "qvq" 前缀的模型使用多模态 API
- **思考模式**: `enable_thinking` 参数为 Qwen3/QwQ/DeepSeek-R1 等模型启用链式思考
- **增量输出**: `incremental_output=True` 是流式处理的必要条件
- **环境变量配置**: 通过 `DASHSCOPE_API_HEADERS` 环境变量自定义请求头
`─────────────────────────────────────────────────`

#### 4.1.3 流式响应解析 (第 300-485 行)

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
    """Given a DashScope streaming response generator, extract the content
        blocks and usages from it and yield ChatResponse objects."""
    acc_content, acc_thinking_content = "", ""
    acc_tool_calls = collections.defaultdict(dict)
    last_input_objs = {}
    metadata = None
    last_content = None
    usage = None
    response_id: str | None = None

    async for chunk in giter(response):
        if chunk.status_code != HTTPStatus.OK:
            raise RuntimeError(f"Failed to get response from API: {chunk}")

        if response_id is None:
            response_id = getattr(chunk, "request_id", None)

        message = chunk.output.choices[0].message

        # 更新推理内容
        if isinstance(message.get("reasoning_content"), str):
            acc_thinking_content += message["reasoning_content"]

        # 更新文本内容 (可能是 str 或 list)
        if isinstance(message.content, str):
            acc_content += message.content
        elif isinstance(message.content, list):
            for item in message.content:
                if isinstance(item, dict) and "text" in item:
                    acc_content += item["text"]

        # 更新工具调用
        for tool_call in message.get("tool_calls", []):
            index = tool_call.get("index", 0)

            if "id" in tool_call and tool_call["id"] != acc_tool_calls[index].get("id"):
                acc_tool_calls[index]["id"] = (
                    acc_tool_calls[index].get("id", "") + tool_call["id"]
                )

            if "function" in tool_call:
                func = tool_call["function"]
                if "name" in func:
                    acc_tool_calls[index]["name"] = (
                        acc_tool_calls[index].get("name", "") + func["name"]
                    )
                if "arguments" in func:
                    acc_tool_calls[index]["arguments"] = (
                        acc_tool_calls[index].get("arguments", "")
                        + func["arguments"]
                    )

        # 构建 content blocks
        content_blocks = []

        if acc_thinking_content:
            content_blocks.append(
                ThinkingBlock(type="thinking", thinking=acc_thinking_content)
            )

        if acc_content:
            content_blocks.append(TextBlock(type="text", text=acc_content))

        # 处理工具调用
        for tool_call in acc_tool_calls.values():
            tool_id = tool_call.get("id", "")
            input_str = tool_call.get("arguments")

            if self.stream_tool_parsing:
                repaired_input = _parse_streaming_json_dict(
                    input_str, last_input_objs.get(tool_id)
                )
                last_input_objs[tool_id] = repaired_input
            else:
                repaired_input = {}

            content_blocks.append(ToolUseBlock(
                type="tool_use",
                id=tool_id,
                name=tool_call.get("name", ""),
                input=repaired_input,
                raw_input=input_str,
            ))

            if structured_model:
                metadata = repaired_input

        # 更新 usage
        if chunk.usage:
            usage = ChatUsage(
                input_tokens=chunk.usage.input_tokens,
                output_tokens=chunk.usage.output_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
                metadata=chunk.usage,
            )

        if content_blocks:
            _kwargs = {
                "content": content_blocks,
                "usage": usage,
                "metadata": metadata,
            }
            if response_id:
                _kwargs["id"] = response_id
            yield ChatResponse(**_kwargs)
            last_content = copy.deepcopy(content_blocks)

    # 处理最后的工具调用
    if not self.stream_tool_parsing and last_content and acc_tool_calls:
        metadata = None
        for block in last_content:
            if block.get("type") == "tool_use":
                block["input"] = _json_loads_with_repair(
                    str(block.get("raw_input") or "{}")
                )
                if structured_model:
                    metadata = block["input"]

        _final_kwargs = {
            "content": last_content,
            "usage": usage,
            "metadata": metadata,
        }
        if response_id:
            _final_kwargs["id"] = response_id
        yield ChatResponse(**_final_kwargs)
```

#### 4.1.4 非流式响应解析 (第 487-591 行)

```python
async def _parse_dashscope_generation_response(
    self,
    start_datetime: datetime,
    response: Union[GenerationResponse, MultiModalConversationResponse],
    structured_model: Type[BaseModel] | None = None,
) -> ChatResponse:
    if response.status_code != 200:
        raise RuntimeError(response)

    content_blocks = []
    metadata = None

    message = response.output.choices[0].message
    content = message.get("content")

    if response.output.choices[0].message.get("content") not in [None, "", []]:
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    content_blocks.append(TextBlock(type="text", text=item["text"]))
        else:
            content_blocks.append(TextBlock(type="text", text=content))

    if message.get("tool_calls"):
        for tool_call in message["tool_calls"]:
            input_ = _json_loads_with_repair(
                tool_call["function"].get("arguments", "{}") or "{}"
            )
            content_blocks.append(ToolUseBlock(
                type="tool_use",
                name=tool_call["function"]["name"],
                input=input_,
                id=tool_call["id"],
            ))
            if structured_model:
                metadata = input_

    usage = None
    if response.usage:
        usage = ChatUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            time=(datetime.now() - start_datetime).total_seconds(),
            metadata=response.usage,
        )

    resp_kwargs = {
        "content": content_blocks,
        "usage": usage,
        "metadata": metadata,
    }
    response_id = getattr(response, "request_id", None)
    if response_id:
        resp_kwargs["id"] = response_id

    return ChatResponse(**resp_kwargs)
```

#### 4.1.5 工具格式化 (第 593-642 行)

```python
def _format_tools_json_schemas(
    self,
    schemas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Format the tools JSON schema into required format for DashScope API."""
    for value in schemas:
        if (
            not isinstance(value, dict)
            or "type" not in value
            or value["type"] != "function"
            or "function" not in value
        ):
            raise ValueError(
                f"Each schema must be a dict with 'type' as 'function' "
                f"and 'function' key, got {value}",
            )
    return schemas

def _format_tool_choice(
    self,
    tool_choice: Literal["auto", "none", "required"] | str | None,
) -> str | dict | None:
    """Format tool_choice parameter for API compatibility."""
    if tool_choice is None:
        return None
    if tool_choice in ["auto", "none"]:
        return tool_choice
    if tool_choice == "required":
        return "auto"  # DashScope 不支持 required
    return {"type": "function", "function": {"name": tool_choice}}
```

---

## 5. Anthropic 模型适配器分析

### 5.1 主要特性

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_anthropic_model.py`

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

### 6.1 GeminiChatModel

**文件**: `_gemini_model.py` | **导出名**: `GeminiChatModel`

- Google Gemini Pro/Flash 支持
- 多模态输入支持（文本 + 图像）
- Vertex AI 和 Gemini API 两种模式
- 构造函数参数：`model_name`, `api_key`, `stream`, `project`, `location` 等

### 6.2 OllamaChatModel

**文件**: `_ollama_model.py` | **导出名**: `OllamaChatModel`

- 本地大模型运行（Llama 3、Mistral、Qwen 等开源模型）
- REST API 通信（`http://localhost:11434`）
- 构造函数参数：`model_name`, `stream`, `client_kwargs` 等
- 无需 API Key，适合本地开发和隐私敏感场景

### 6.3 AnthropicChatModel

**文件**: `_anthropic_model.py` | **导出名**: `AnthropicChatModel`

- Claude 系列模型支持（Claude 3.5/4 等）
- 支持 extended thinking（扩展思考）
- 构造函数参数：`model_name`, `api_key`, `stream`, `thinking_config` 等

### 6.4 TrinityChatModel

**文件**: `_trinity_model.py` | **导出名**: `TrinityChatModel`

- 与 Trinity-RFT 框架集成
- 用于 RL/SFT 训练场景
- 构造函数参数：`model_name`, `stream`, `server_url` 等

---

## 7. 工具调用机制详解

### 7.1 核心工具函数

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/_utils/_common.py`

#### 7.1.1 JSON 修复与解析 (第 31-94 行)

```python
def _json_loads_with_repair(
    json_str: str,
) -> dict:
    """The given json_str maybe incomplete, e.g. '{"key', so we need to
    repair and load it into a Python object.

    This function is primarily used for parsing the streaming output
    of the argument field in `tool_use`, where partial JSON needs
    to be repaired and loaded.

    Args:
        json_str (`str`):
            The JSON string to parse, which may be incomplete or malformed.

    Returns:
        `dict`:
            A dictionary parsed from the JSON string after repair attempts.
            Returns an empty dict if all repair attempts fail.
    """
    try:
        repaired = repair_json(json_str, stream_stable=True)
        result = json.loads(repaired)
        if isinstance(result, dict):
            return result

    except Exception:
        if len(json_str) > 100:
            log_str = json_str[:100] + "..."
        else:
            log_str = json_str

        logger.warning(
            "Failed to load JSON dict from string: %s. Returning empty dict "
            "instead.",
            log_str,
        )

    return {}


def _parse_streaming_json_dict(
    json_str: str,
    last_input: dict | None = None,
) -> dict:
    """Parse a streaming JSON dict without regressing on incomplete chunks.

    If the current chunk already forms a valid JSON dict, prefer it directly.
    Otherwise, fall back to repaired JSON and keep the previous parsed value
    only when repair would shrink the intermediate structure.
    """
    json_str = json_str or "{}"
    try:
        result = json.loads(json_str)
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    repaired_input = _json_loads_with_repair(json_str)
    last_input = last_input or {}
    if len(json.dumps(last_input)) > len(json.dumps(repaired_input)):
        return last_input
    return repaired_input
```

#### 7.1.2 从 Pydantic BaseModel 创建工具 (第 266-322 行)

```python
def _create_tool_from_base_model(
    structured_model: Type[BaseModel],
    tool_name: str = "generate_structured_output",
) -> Dict[str, Any]:
    """Create a function tool definition from a Pydantic BaseModel.
    This function converts a Pydantic BaseModel class into a tool definition
    that can be used with function calling API.
    """
    schema = structured_model.model_json_schema()

    _remove_title_field(schema)
    tool_definition = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": "Generate the required structured output with "
            "this function",
            "parameters": schema,
        },
    }
    return tool_definition
```

`★ Insight ─────────────────────────────────────`
- **流式 JSON 解析**: `_parse_streaming_json_dict` 实现了"不倒退"原则——只有当修复后的 JSON 不缩小结构时才更新
- **增量反馈**: 这个机制让工具可以在完整参数到达前就开始部分工作
- **结构化输出转换**: `_create_tool_from_base_model` 将 Pydantic 模型转换为工具定义，实现结构化输出
`─────────────────────────────────────────────────`

### 7.2 工具调用流程图

```
User Message: "What's the weather in Paris?"
    │
    ▼
┌─────────────────────────────────────────┐
│  Formatter: 消息格式化                   │
│  - 将消息转为 provider 格式              │
│  - 添加工具 schema                      │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  Model.__call__                          │
│  - 验证参数                             │
│  - 组装 API 请求                        │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  API 调用                               │
│  - stream=True → 增量响应               │
│  - stream=False → 完整响应              │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  响应解析                               │
│  - 提取文本内容                         │
│  - 提取工具调用                         │
│  - 流式: 增量 JSON 修复                │
└─────────────────────────────────────────┘
    │
    ▼
ChatResponse(content=[
    ToolUseBlock(
        type="tool_use",
        name="get_weather",
        input={"location": "Paris"},
        ...
    )
])
    │
    ▼
┌─────────────────────────────────────────┐
│  智能体执行工具                         │
│  - 调用工具函数                         │
│  - 获取结果                             │
│  - 将结果作为新消息发送                 │
└─────────────────────────────────────────┘
    │
    ▼
下一轮对话循环
```

### 7.3 工具 schema 格式

AgentScope 使用标准的 OpenAI 工具格式：

```python
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
```

### 7.4 工具选择模式对比

| 模式 | OpenAI | DashScope | 说明 |
|------|--------|-----------|------|
| `auto` | 支持 | 支持 | 模型自行决定是否使用工具 |
| `none` | 支持 | 支持 | 禁止使用工具 |
| `required` | 支持 | 转为 `auto` | 必须使用工具 |
| `函数名` | 支持 | 支持 | 强制使用特定工具 |

---

## 8. Token 计数机制

### 8.1 Token 计数器基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/token/_token_base.py` (第 1-16 行)

```python
class TokenCounterBase:
    """The base class for token counting."""

    @abstractmethod
    async def count(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> int:
        """Count the number of tokens by the given model and messages."""
```

### 8.2 OpenAI Token 计数器

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/token/_openai_token_counter.py`

#### 8.2.1 图像 Token 计算 (第 18-48 行)

```python
def _calculate_tokens_for_high_quality_image(
    base_tokens: int,
    tile_tokens: int,
    width: int,
    height: int,
) -> int:
    """Calculate the number of tokens for a high-quality image, which follows
    https://platform.openai.com/docs/guides/images-vision?api-mode=chat#calculating-costs
    """
    # Step1: scale to fit within a 2048x2048 box
    if width > 2048 or height > 2048:
        ratio = min(2048 / width, 2048 / height)
        width = int(width * ratio)
        height = int(height * ratio)

    # Step2: Scale to make the shortest side 768 pixels
    shortest_side = min(width, height)
    if shortest_side != 768:
        ratio = 768 / shortest_side
        width = int(width * ratio)
        height = int(height * ratio)

    # Step3: Calculate how many 512px tiles are needed
    tiles_width = (width + 511) // 512
    tiles_height = (height + 511) // 512
    total_tiles = tiles_width * tiles_height

    # Step4: Calculate the total tokens
    total_tokens = (total_tiles * tile_tokens) + base_tokens

    return total_tokens
```

#### 8.2.2 OpenAITokenCounter 实现 (第 297-384 行)

```python
class OpenAITokenCounter(TokenCounterBase):
    """The OpenAI token counting class."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    async def count(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] = None,
        **kwargs: Any,
    ) -> int:
        """Count the token numbers of the given messages."""
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(self.model_name)
        except KeyError:
            encoding = tiktoken.get_encoding("o200k_base")

        tokens_per_message = 3
        tokens_per_name = 1

        # every reply is primed with <|start|>assistant<|message|>
        num_tokens = 3
        for message in messages:
            num_tokens += tokens_per_message
            for key, value in message.items():
                # 考虑视觉模型
                if key == "content" and isinstance(value, list):
                    num_tokens += _count_content_tokens_for_openai_vision_model(
                        self.model_name,
                        value,
                        encoding,
                    )

                elif isinstance(value, str):
                    num_tokens += len(encoding.encode(value))

                elif value is None:
                    continue

                elif key == "tool_calls":
                    num_tokens += len(
                        encoding.encode(
                            json.dumps(value, ensure_ascii=False),
                        ),
                    )

                else:
                    raise TypeError(
                        f"Invalid type {type(value)} in the {key} field: {value}",
                    )

                if key == "name":
                    num_tokens += tokens_per_name

        if tools:
            num_tokens += _calculate_tokens_for_tools(
                self.model_name,
                tools,
                encoding,
            )

        return num_tokens
```

`★ Insight ─────────────────────────────────────`
- **视觉模型支持**: Token 计数器不仅处理文本，还计算图像的 Token 数量
- **动态编码选择**: 根据模型名称选择合适的 tiktoken 编码器
- **工具 Token 计算**: 专门有 `_calculate_tokens_for_tools` 函数计算工具定义的 Token
`─────────────────────────────────────────────────`

### 8.3 其他 Token 计数器

| 计数器 | 文件 | 说明 |
|--------|------|------|
| `AnthropicTokenCounter` | `_anthropic_token_counter.py` | Claude 系列 Token 计数 |
| `CharTokenCounter` | `_char_token_counter.py` | 基于字符数的简单计数 |
| `GeminiTokenCounter` | `_gemini_token_counter.py` | Gemini Token 计数 |
| `HuggingFaceTokenCounter` | `_huggingface_token_counter.py` | HuggingFace 模型 Token 计数 |

---

## 9. Embedding 模块

### 9.1 Embedding 模型基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/embedding/_embedding_base.py` (第 1-45 行)

```python
class EmbeddingModelBase:
    """Base class for embedding models."""

    model_name: str
    """The embedding model name"""

    supported_modalities: list[str]
    """The supported data modalities, e.g. "text", "image", "video"."""

    dimensions: int
    """The dimensions of the embedding vector."""

    def __init__(
        self,
        model_name: str,
        dimensions: int,
    ) -> None:
        self.model_name = model_name
        self.dimensions = dimensions

    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the embedding API with the given arguments."""
        raise NotImplementedError(
            f"The {self.__class__.__name__} class does not implement "
            f"the __call__ method.",
        )
```

### 9.2 Embedding 响应类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/embedding/_embedding_response.py` (第 1-33 行)

```python
@dataclass
class EmbeddingResponse(DictMixin):
    """The embedding response class."""

    embeddings: List[Embedding]
    """The embedding data"""

    id: str = field(default_factory=lambda: _get_timestamp(True))
    """The identity of the embedding response"""

    created_at: str = field(default_factory=_get_timestamp)
    """The timestamp of the embedding response creation"""

    type: Literal["embedding"] = field(default_factory=lambda: "embedding")
    """The type of the response, must be `embedding`."""

    usage: EmbeddingUsage | None = field(default_factory=lambda: None)
    """The usage of the embedding model API invocation, if available."""

    source: Literal["cache", "api"] = field(default_factory=lambda: "api")
    """If the response comes from the cache or the API."""
```

### 9.3 Embedding 使用统计类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/embedding/_embedding_usage.py` (第 1-20 行)

```python
@dataclass
class EmbeddingUsage(DictMixin):
    """The usage of an embedding model API invocation."""

    time: float
    """The time used in seconds."""

    tokens: int | None = field(default_factory=lambda: None)
    """The number of tokens used, if available."""

    type: Literal["embedding"] = field(default_factory=lambda: "embedding")
    """The type of the usage, must be `embedding`."""
```

### 9.4 OpenAI Embedding 实现

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/embedding/_openai_embedding.py` (第 1-109 行)

```python
class OpenAITextEmbedding(EmbeddingModelBase):
    """OpenAI text embedding model class."""

    supported_modalities: list[str] = ["text"]
    """This class only supports text input."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        dimensions: int = 1024,
        embedding_cache: EmbeddingCacheBase | None = None,
        **kwargs: Any,
    ) -> None:
        import openai

        super().__init__(model_name, dimensions)

        self.client = openai.AsyncClient(api_key=api_key, **kwargs)
        self.embedding_cache = embedding_cache

    async def __call__(
        self,
        text: List[str | TextBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the OpenAI embedding API."""
        gather_text = []
        for _ in text:
            if isinstance(_, dict) and "text" in _:
                gather_text.append(_["text"])
            elif isinstance(_, str):
                gather_text.append(_)
            else:
                raise ValueError(
                    "Input text must be a list of strings or TextBlock dicts.",
                )

        kwargs = {
            "input": gather_text,
            "model": self.model_name,
            "dimensions": self.dimensions,
            "encoding_format": "float",
            **kwargs,
        }

        if self.embedding_cache:
            cached_embeddings = await self.embedding_cache.retrieve(
                identifier=kwargs,
            )
            if cached_embeddings:
                return EmbeddingResponse(
                    embeddings=cached_embeddings,
                    usage=EmbeddingUsage(
                        tokens=0,
                        time=0,
                    ),
                    source="cache",
                )

        start_time = datetime.now()
        response = await self.client.embeddings.create(**kwargs)
        time = (datetime.now() - start_time).total_seconds()

        if self.embedding_cache:
            await self.embedding_cache.store(
                identifier=kwargs,
                embeddings=[_.embedding for _ in response.data],
            )

        return EmbeddingResponse(
            embeddings=[_.embedding for _ in response.data],
            usage=EmbeddingUsage(
                tokens=response.usage.total_tokens,
                time=time,
            ),
        )
```

`★ Insight ─────────────────────────────────────`
- **缓存支持**: `OpenAITextEmbedding` 支持嵌入结果缓存，避免重复 API 调用
- **多模态支持**: 基类定义了 `supported_modalities` 和 `dimensions` 属性
- **响应溯源**: `EmbeddingResponse.source` 字段标识数据来自缓存还是 API
`─────────────────────────────────────────────────`

### 9.5 Embedding 模型一览

| 模型 | 文件 | 说明 |
|------|------|------|
| OpenAI | `_openai_embedding.py` | text-embedding-3-small 等 |
| DashScope | `_dashscope_embedding.py` | 阿里文本嵌入 |
| Gemini | `_gemini_embedding.py` | Google 嵌入 |
| Ollama | `_ollama_embedding.py` | 本地模型嵌入 |

---

## 10. 代码示例

### 10.0 前提条件与配置

使用模型适配器前，需要配置 API 密钥。AgentScope 支持以下配置方式：

**方式一：环境变量（推荐）**

```python showLineNumbers
import os
os.environ["OPENAI_API_KEY"] = "sk-..."      # OpenAI
os.environ["DASHSCOPE_API_KEY"] = "sk-..."   # 阿里云 DashScope
os.environ["ANTHROPIC_API_KEY"] = "sk-..."  # Anthropic
```

**方式二：直接传入参数**

```python showLineNumbers
model = OpenAIChatModel(
    model_name="gpt-4",
    api_key="sk-...",  # 直接传入 API key
)
```

**方式三：Azure OpenAI**

```python showLineNumbers
model = OpenAIChatModel(
    model_name="gpt-4",
    api_key="your-azure-key",
    client_type="azure",
    client_kwargs={
        "azure_endpoint": "https://your-resource.openai.azure.com/",
        "api_version": "2024-02-01",
    },
)
```

### 10.1 使用 OpenAI 模型

```python showLineNumbers
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter

# 创建模型实例
# API key 可以通过以下方式提供:
# 1. 直接传入 api_key 参数
# 2. 设置环境变量 OPENAI_API_KEY
model = OpenAIChatModel(
    model_name="gpt-4",
    api_key="your-api-key",  # 可选: 从环境变量 OPENAI_API_KEY 读取
    stream=True,
)

# 创建格式化器
formatter = OpenAIChatFormatter()

# 准备消息
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is the capital of France?"},
]

# 异步调用 (需在 async 函数内执行)
response = await model(messages)
print(f"Response: {response.content}")
```

**预期输出**：
```
Response: The capital of France is Paris.
```

### 10.2 流式输出处理

```python showLineNumbers
import os

async def stream_chat():
    # API key 从环境变量读取
    model = OpenAIChatModel(
        model_name="gpt-4",
        api_key=os.getenv("OPENAI_API_KEY"),  # 或直接传入 api_key="sk-..."
        stream=True,
    )

    messages = [
        {"role": "user", "content": "Write a story about a robot."},
    ]

    # 流式处理响应 - 注意：model() 返回 AsyncGenerator，不需 await
    async for chunk in model(messages):
        for block in chunk.content:
            if block["type"] == "text":
                print(block["text"], end="", flush=True)
```

**预期输出**：
```
Once upon a time, in a distant galaxy...
```

### 10.3 工具调用

```python showLineNumbers
from agentscope.model import OpenAIChatModel

# 创建模型实例 (api_key 可从环境变量 OPENAI_API_KEY 读取)
model = OpenAIChatModel(
    model_name="gpt-4",
    api_key="your-api-key",
)

# 定义工具 (使用 OpenAI 工具调用格式)
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

**预期输出**：
```
Tool: get_weather
Args: {'location': 'Paris'}
```

### 10.4 结构化输出

```python showLineNumbers
from pydantic import BaseModel
from typing import Literal

class WeatherResponse(BaseModel):
    location: str
    temperature: float
    unit: Literal["celsius", "fahrenheit"]
    condition: str

# 创建模型实例
model = OpenAIChatModel(
    model_name="gpt-4",
    api_key="your-api-key",  # 或从环境变量 OPENAI_API_KEY 读取
)

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

**预期输出**：
```
Tokyo: 22.5celsius
```

### 10.5 DashScope 多模态模型

```python showLineNumbers
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

**预期输出**：
```
The image shows a beautiful sunset over the ocean.
```

---

## 本章关联

### 与其他模块的关系

| 关联模块 | 关联内容 | 参考位置 |
|----------|----------|----------|
| [Agent 模块深度剖析](module_agent_deep.md) | Agent 如何调用 Model 进行推理，ReActAgent 的记忆压缩如何触发 Token 计数 | 第 3.2 节 `__call__()`、第 4.4 节 `_acting()` |
| [Memory/RAG 模块深度剖析](module_memory_rag_deep.md) | Token 计数器在记忆压缩中的应用，Embedding 模型在 RAG 检索中的角色 | 第 8 章 Token 计数、第 9 章 Embedding |
| [Tool/MCP 模块深度剖析](module_tool_mcp_deep.md) | 模型适配器的工具调用机制 (`tool_choice`、`structured_model`) 与 Toolkit 的协作 | 第 7 章工具调用机制 |
| [Pipeline/基础设施模块深度剖析](module_pipeline_infra_deep.md) | Formatter 如何将 Msg 对象转换为各模型 API 所需的格式 | 第 3 章 Formatter 消息格式化 |
| [最佳实践参考](reference_best_practices.md) | 模型选型建议、多模型负载均衡、Token 成本控制策略 | 模型选择章节 |

### 前置知识

- **Pydantic**: 如不熟悉 `BaseModel` 和 `Field`，建议先阅读 [Pydantic 文档](https://docs.pydantic.dev/)
- **LLM API**: 建议至少了解 OpenAI API 的基本调用方式
- **类型注解**: 需要理解 `TypeVar`、`Generic`、`Literal` 等泛型用法

### 后续学习建议

1. 完成本模块练习题后，建议继续学习 [Tool/MCP 模块](module_tool_mcp_deep.md)，深入理解工具调用和结构化输出
2. 如需构建 RAG 应用，建议学习 [Memory/RAG 模块](module_memory_rag_deep.md) 的 Embedding 和向量存储
3. 如需适配自定义模型，可参考本模块中 `ChatModelBase` 的设计，实现新的模型适配器

---

### 边界情况与陷阱

#### Critical: 模型配置的必填参数

```python
# 不同模型适配器有不同的必填参数
# DashScope 需要 api_key
config = DashScopeConfig(api_key="sk-...")

# Ollama 需要 host
config = OllamaConfig(host="http://localhost:11434")

# 问题：遗漏参数会导致运行时错误
config = DashScopeConfig()  # Missing api_key
```

#### High: 流式输出的异常处理

```python
# 流式输出可能在中间中断
stream = await model.chat(messages, stream=True)

try:
    async for chunk in stream:
        yield chunk
except Exception as e:
    # 网络中断或 API 错误
    # 此时已经 yield 了部分内容
    # 调用方可能收到不完整的响应
```

**解决方案**：始终检查最终响应的完整性。

#### High: API 限流处理

```python
# 模型 API 有速率限制
# DashScope: 限制并发数
# OpenAI: 限制 RPM/TPM

# 问题：超出限制会返回 429 错误
# 解决方案：实现指数退避重试
for attempt in range(3):
    try:
        return await model.chat(messages)
    except RateLimitError:
        await asyncio.sleep(2 ** attempt)
```

#### Medium: Token 计数的近似性

```python
# Token 计数器与实际 API 返回的数量可能不同
# tiktoken vs OpenAI tokenizer

token_count = count_tokens("Hello world")  # tiktoken: 2
# 但 API 返回可能是 3（取决于模型的分词器）

# 问题：可能导致上下文窗口计算不准确
```

#### Medium: 多模态模型的消息格式

```python
# 多模态输入需要特定格式
message = {
    "role": "user",
    "content": [
        {"type": "text", "text": "描述这张图片"},
        {"type": "image_url", "image_url": {"url": "https://..."}}
    ]
}

# 问题：格式错误会导致 API 返回 400 错误
# 不同模型有不同的多模态格式要求
```

---

### 性能考量

#### 模型延迟对比

| 模型 | 延迟 | 吞吐量 | 适用场景 |
|------|------|--------|----------|
| GPT-4o | ~2s | 低 | 复杂推理 |
| GPT-4o-mini | ~1s | 中 | 通用任务 |
| Claude 3.5 | ~1.5s | 中 | 长上下文 |
| Ollama (本地) | ~100ms | 高 | 快速迭代 |

#### 流式 vs 非流式

```python
# 流式输出有额外的协议开销
# 首字节延迟更高

# 非流式：等待完整响应后返回
result = await model.chat(messages)  # 2s 延迟

# 流式：首字节更快，但总时间相同
async for chunk in model.chat(messages, stream=True):
    print(chunk)  # 更快看到开始
```

#### Token 计算性能

```python
# Token 计数有计算成本
# tiktoken: ~0.01ms/调用
# 但批量计算可以使用 C 优化版本

# 优化建议：
# - 仅在必要时计算（如接近上下文限制）
# - 缓存重复计算的 token 数
```

---

## 11. 练习题

### 11.1 基础题

1. **分析 `ChatModelBase` 基类的设计意图，参考 `_model_base.py:13-77`。**

2. **OpenAI 和 DashScope 模型适配器在处理流式输出时有何异同？**

3. **解释 `tool_choice` 参数的作用，并说明其可选值。**

### 11.2 进阶题

4. **分析结构化输出的实现机制，以 OpenAI 适配器为例。**

5. **设计一个模型适配器，支持同时调用多个模型并合并结果。**

6. **分析 DashScope 适配器如何自动选择 Generation 和 MultimodalConversation API。**

### 11.3 挑战题

7. **实现一个自定义 Token 计数器，支持滑动窗口截断。**

8. **分析 AgentScope 的模型适配器与 LangChain 的 Model IO 有何异同。**

9. **设计一个模型代理，实现请求重试、负载均衡和故障转移。**

### 练习 11.10: 流式响应逐块解析 [基础]

**题目描述**：
使用 OpenAI 模型适配器发起流式请求，预测并验证以下代码的输出内容。

```python
import asyncio
from agentscope.model import OpenAIChatModel

model = OpenAIChatModel(model_name="gpt-4o-mini")

async def main():
    response = await model([{"role": "user", "content": "Say hello in one word"}])
    async for chunk in response:
        print(f"chunk: {chunk}")

asyncio.run(main())
```

**预期输出/行为**：
`chunk.content` 的累积结果为 `"Hello"`（或类似单词）。每个 chunk 是 `ChatResponse` 对象，`chunk.delta` 或 `chunk.content` 包含增量文本。

<details>
<summary>参考答案</summary>

```python
import asyncio
from agentscope.model import OpenAIChatModel

model = OpenAIChatModel(model_name="gpt-4o-mini")

async def main():
    response = await model([{"role": "user", "content": "Say hello in one word"}])
    accumulated = ""
    async for chunk in response:
        if hasattr(chunk, "delta") and chunk.delta:
            accumulated += chunk.delta
            print(f"delta: '{chunk.delta}', accumulated: '{accumulated}'")
        elif hasattr(chunk, "content") and chunk.content:
            accumulated += chunk.content
            print(f"content: '{chunk.content}', accumulated: '{accumulated}'")

asyncio.run(main())
# 输出示例:
# delta: 'Hel', accumulated: 'Hel'
# delta: 'lo', accumulated: 'Hello'
```
</details>

### 练习 11.11: 结构化输出字段验证 [基础]

**题目描述**：
使用 `structured_model` 参数请求 Pydantic 模型输出，验证返回的对象类型和字段访问方式。

```python
from pydantic import BaseModel
from agentscope.model import OpenAIChatModel
import asyncio

class WeatherInfo(BaseModel):
    city: str
    temperature: float
    unit: str = "celsius"

model = OpenAIChatModel(model_name="gpt-4o")

async def main():
    response = await model(
        [{"role": "user", "content": "What's the weather in Beijing?"}],
        structured_model=WeatherInfo
    )
    result = response.content  # 期望是 WeatherInfo 实例
    print(f"type: {type(result)}")
    print(f"city: {result.city}, temp: {result.temperature}°{result.unit}")

asyncio.run(main())
```

**预期输出/行为**：
`type(result)` 为 `WeatherInfo`，可直接通过 `.city`、`.temperature`、`.unit` 访问字段。

<details>
<summary>参考答案</summary>

```python
from pydantic import BaseModel
from agentscope.model import OpenAIChatModel
import asyncio

class WeatherInfo(BaseModel):
    city: str
    temperature: float
    unit: str = "celsius"

model = OpenAIChatModel(model_name="gpt-4o")

async def main():
    response = await model(
        [{"role": "user", "content": "What's the weather in Beijing?"}],
        structured_model=WeatherInfo
    )
    result = response.content  # OpenAI 适配器内部调用 response.parsed 或 model.parse() 转换
    assert isinstance(result, WeatherInfo), f"Expected WeatherInfo, got {type(result)}"
    print(f"type: {type(result)}")
    print(f"city: {result.city}, temp: {result.temperature}°{result.unit}")

asyncio.run(main())
# 预期输出:
# type: <class 'WeatherInfo'>
# city: Beijing, temp: 22.5°celsius
```
</details>

### 练习 11.12: 模型配置错误诊断 [中级]

**题目描述**：
以下代码运行时出现 `ValueError: model_name must be specified` 或类似错误。请阅读模型适配器源码（参考 `_openai_model.py`），找出所有可能导致此错误的配置问题，并修复代码。

```python
from agentscope.model import OpenAIChatModel
import os

# 尝试使用环境变量中的 API key
api_key = os.getenv("OPENAI_API_KEY")

model = OpenAIChatModel(
    api_key=api_key,
    # 缺少 model_name
)

import asyncio
async def main():
    result = await model([{"role": "user", "content": "Hi"}])
    print(result.content)

asyncio.run(main())
```

**预期输出/行为**：
修复后代码正常运行，输出模型生成的文本。

<details>
<summary>参考答案</summary>

```python
# 错误原因：OpenAIChatModel 要求 model_name 参数，不能为空。
# 其他常见配置错误：
# 1. api_key 为 None（环境变量未设置）
# 2. base_url 拼写错误（应为 base_url 而非 url）
# 3. stream 参数类型错误（应为 bool 而非 string）

from agentscope.model import OpenAIChatModel
import os

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OPENAI_API_KEY environment variable not set")

model = OpenAIChatModel(
    api_key=api_key,
    model_name="gpt-4o-mini",  # 必须指定
    # base_url="https://api.openai.com/v1",  # 可选，默认值已填充
)

import asyncio
async def main():
    result = await model([{"role": "user", "content": "Hi"}])
    print(result.content)

asyncio.run(main())
```
</details>

### 练习 11.13: 多模型自动 Fallback 策略 [挑战]

**题目描述**：
设计一个 `ModelWithFallback` 类，实现：当主模型（GPT-4o）调用失败时，自动切换到备用模型（GPT-4o-mini）；连续 3 次失败后降级到本地模型（如 Ollama）。每次切换需记录日志。

**预期输出/行为**：
运行模拟故障注入的测试用例，验证模型按预期顺序切换。

<details>
<summary>参考答案</summary>

```python
import asyncio
import logging
from typing import Any
from agentscope.model import OpenAIChatModel, ChatResponse

logger = logging.getLogger(__name__)

class ModelWithFallback:
    """支持自动降级的模型封装。"""

    def __init__(
        self,
        primary_model: OpenAIChatModel,
        fallback_model: OpenAIChatModel,
        local_model: Any = None,
    ):
        self.models = [primary_model, fallback_model, local_model]
        self.current_index = 0
        self.max_retries_per_model = 3

    async def __call__(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        """发起请求，失败时自动降级。"""
        attempts = 0
        while self.current_index < len(self.models):
            model = self.models[self.current_index]
            try:
                logger.info(f"尝试模型 {self.current_index}: {type(model).__name__}")
                response = await model(messages, **kwargs)
                logger.info(f"模型 {self.current_index} 成功")
                return response
            except Exception as e:
                attempts += 1
                logger.warning(f"模型 {self.current_index} 失败 ({attempts}/{self.max_retries_per_model}): {e}")
                if attempts >= self.max_retries_per_model:
                    self.current_index += 1
                    attempts = 0
                    logger.info(f"切换到下一个模型，当前索引: {self.current_index}")

        raise RuntimeError("所有模型均不可用")

# 使用示例
primary = OpenAIChatModel(model_name="gpt-4o", api_key="sk-...")
fallback = OpenAIChatModel(model_name="gpt-4o-mini", api_key="sk-...")

model_with_fb = ModelWithFallback(primary, fallback)

async def main():
    response = await model_with_fb([{"role": "user", "content": "Hello"}])
    print(response.content)

asyncio.run(main())
```
</details>

---

## 参考答案

### 11.1 基础题

**1. 分析 `ChatModelBase` 基类的设计意图。**

`ChatModelBase` 是 AgentScope 所有模型适配器的抽象基类，其设计意图：

- **统一接口**: 所有模型适配器（OpenAI、DashScope、Anthropic 等）实现相同的 `__call__()` 接口，Agent 代码无需关心底层模型差异
- **流式/非流式统一**: `__call__()` 返回 `ChatResponse | AsyncGenerator[ChatResponse, None]`，调用方通过 `isinstance()` 或 `inspect.isasyncgen()` 判断响应类型
- **工具调用标准化**: 基类定义 `tool_choice` 参数，子类负责将通用选项转换为各自 API 的格式
- **结构化输出抽象**: `structured_model` 参数接受 Pydantic `BaseModel`，子类负责转换为各自 API 支持的格式（如 OpenAI 的 JSON Schema、DashScope 的 tool_call）

**核心抽象方法**:
```python
@abstractmethod
async def __call__(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    tool_choice: Literal["auto", "none", "required"] | str | None = None,
    structured_model: Type[BaseModel] | None = None,
    **kwargs: Any,
) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
```

**2. OpenAI 和 DashScope 模型适配器在处理流式输出时有何异同？**

**相同之处**:
- 都通过 `stream=True` 参数启用流式输出
- 都返回 `AsyncGenerator[ChatResponse, None]`
- 都使用增量解析策略（逐 chunk 累积内容）
- 都在 `finally` 块中确保生成器正确关闭

**不同之处**:

| 维度 | OpenAI | DashScope |
|------|--------|-----------|
| **流式 API** | `openai.AsyncClient.chat.completions.create(stream=True)` | `dashscope.AioGeneration.call()` 或 `AioMultiModalConversation.call()` |
| **增量输出** | `stream=True` 自动启用 | 需要显式设置 `incremental_output=True` |
| **工具调用解析** | 支持 `stream_tool_parsing` | 同样支持，但 API 格式不同 |
| **多模态** | 通过 `content` 数组支持 | 通过 `MultiModalConversation` 专门 API 支持 |
| **思考模式** | 通过 `reasoning_content` 字段 | 通过 `enable_thinking` 参数 |

**3. 解释 `tool_choice` 参数的作用，并说明其可选值。**

`tool_choice` 控制模型在响应时是否使用工具：

| 值 | 作用 | 说明 |
|----|------|------|
| `"auto"` | 模型自行决定 | 默认行为，模型根据上下文判断是否调用工具 |
| `"none"` | 禁止调用工具 | 强制模型生成纯文本回复 |
| `"required"` | 必须调用工具 | 强制模型至少调用一个工具 |
| `"函数名"` | 强制调用指定工具 | 如 `"get_weather"`，模型必须调用该工具 |

**注意事项**:
- DashScope 不支持 `"required"`，会自动转换为 `"auto"`
- 当 `structured_model` 参数存在时，`tool_choice` 和 `tools` 会被忽略

---

### 11.2 进阶题

**4. 分析结构化输出的实现机制，以 OpenAI 适配器为例。**

OpenAI 适配器的结构化输出通过两步实现：

**步骤一: 创建工具 Schema** (`_common.py:266`):
```python
def _create_tool_from_base_model(model: Type[BaseModel]) -> dict:
    """将 Pydantic BaseModel 转换为 OpenAI 工具 Schema。"""
    schema = model.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": model.__name__,
            "description": schema.get("description", ""),
            "parameters": schema,
        },
    }
```

**步骤二: 强制工具调用** (`_openai_model.py`):
- 将 `structured_model` 转换为工具 Schema
- 设置 `tool_choice = {"type": "function", "function": {"name": model.__name__}}`
- 这强制模型返回符合该 Schema 的 JSON 对象

**步骤三: 解析响应**:
- 从 `tool_calls` 中提取 `function.arguments`
- 使用 `_json_loads_with_repair()` 解析 JSON（处理流式输出中的不完整 JSON）
- 将解析结果存入 `ChatResponse.metadata["parsed"]`

**5. 设计一个模型适配器，支持同时调用多个模型并合并结果。**

```python
from agentscope.model import ChatModelBase
from agentscope.message import ChatResponse

# 注意：AgentScope 没有 model_register 注册机制，直接实例化即可
class EnsembleChatModel(ChatModelBase):
    """多模型集成适配器，同时调用多个模型并投票选择最佳结果。"""

    def __init__(
        self,
        models: list[ChatModelBase],
        strategy: str = "vote",  # "vote" | "concat" | "best"
        **kwargs: Any,
    ) -> None:
        super().__init__("ensemble", stream=False)
        self.models = models
        self.strategy = strategy

    async def __call__(self, messages, **kwargs):
        # 并发调用所有模型
        tasks = [model(messages, **kwargs) for model in self.models]
        responses = await asyncio.gather(*tasks)

        if self.strategy == "vote":
            # 简单投票: 选择出现次数最多的响应
            contents = [r.content for r in responses]
            best = max(set(contents), key=contents.count)
            return ChatResponse(
                content=best,
                metadata={"votes": {c: contents.count(c) for c in set(contents)}},
            )

        elif self.strategy == "concat":
            # 拼接所有响应
            combined = "\n---\n".join([r.content for r in responses])
            return ChatResponse(content=combined)

        elif self.strategy == "best":
            # 选择最长的响应（假设更详细 = 更好）
            best = max(responses, key=lambda r: len(r.content))
            return best

# 使用
ensemble = EnsembleChatModel(
    models=[openai_model, dashscope_model, anthropic_model],
    strategy="vote",
)
```

**6. 分析 DashScope 适配器如何自动选择 Generation 和 MultimodalConversation API。**

DashScope 适配器在 `__call__()` 中通过模型名称自动选择 API（`_dashscope_model.py` 的 `__call__` 方法内）：

```python
if self.multimodality or (
    self.multimodality is None
    and (
        self.model_name.startswith("qvq")
        or "-vl" in self.model_name
    )
):
    # 多模态模型: 使用 AioMultiModalConversation
    response = await dashscope.AioMultiModalConversation.call(...)
else:
    # 文本模型: 使用 AioGeneration
    response = await dashscope.aigc.generation.AioGeneration.call(...)
```

**判断逻辑**:
1. 如果显式设置 `multimodality=True`，强制使用多模态 API
2. 如果 `multimodality=None`（默认），根据模型名称推断：
   - `"qvq"` 前缀（如 `qvq-max`）→ 多模态
   - `"-vl"` 后缀（如 `qwen-vl-plus`）→ 多模态
3. 否则使用标准 Generation API

---

### 11.3 挑战题

**7. 实现一个自定义 Token 计数器，支持滑动窗口截断。**

```python
from agentscope.token import TokenCounterBase

class SlidingWindowTokenCounter(TokenCounterBase):
    """滑动窗口 Token 计数器，当超过阈值时自动截断最早的消息。"""

    def __init__(
        self,
        model_name: str,
        max_tokens: int = 4096,
        preserve_system: bool = True,
    ) -> None:
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.preserve_system = preserve_system
        self.base_counter = OpenAITokenCounter(model_name)

    async def count(self, messages: list[dict], **kwargs) -> int:
        return await self.base_counter.count(messages, **kwargs)

    def truncate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> list[dict]:
        """滑动窗口截断: 保留最新消息，截断最早的消息。"""
        system_msgs = []
        other_msgs = []

        # 分离系统消息
        for msg in messages:
            if msg.get("role") == "system" and self.preserve_system:
                system_msgs.append(msg)
            else:
                other_msgs.append(msg)

        # 从后向前测试，找到最大保留窗口
        for i in range(len(other_msgs)):
            candidate = system_msgs + other_msgs[i:]
            token_count = asyncio.run(self.count(candidate, tools=tools))
            if token_count <= self.max_tokens:
                return candidate

        # 如果系统消息单独就超限，只保留最后一条
        return other_msgs[-1:] if other_msgs else []
```

**8. 分析 AgentScope 的模型适配器与 LangChain 的 Model IO 有何异同。**

| 维度 | AgentScope | LangChain |
|------|-----------|-----------|
| **核心抽象** | `ChatModelBase.__call__()` | `BaseChatModel.invoke()` / `stream()` |
| **流式输出** | 统一返回 `AsyncGenerator`，由调用方迭代 | 分离 `invoke()` 和 `stream()` 两个方法 |
| **工具调用** | 原生支持，通过 `tool_choice` 和 `tools` 参数 | 通过 `bind_tools()` 和工具链实现 |
| **结构化输出** | 通过 `structured_model` 参数，内部转换为 tool_call | 通过 `with_structured_output()` 方法 |
| **消息格式** | 使用 `Msg` 对象，内部转换为各 API 格式 | 使用 `HumanMessage`/`AIMessage` 等 LangChain 消息类 |
| **适配器覆盖** | OpenAI、DashScope、Anthropic、Gemini、Ollama | 更广泛的生态（100+ 集成） |

**核心差异**: AgentScope 的设计更简洁，将流式/非流式统一到一个 `__call__()` 接口；LangChain 的接口更丰富但复杂度更高。

**9. 设计一个模型代理，实现请求重试、负载均衡和故障转移。**

```python
import random
from agentscope.model import ChatModelBase

class ResilientModelProxy(ChatModelBase):
    """弹性模型代理: 重试 + 负载均衡 + 故障转移。"""

    def __init__(
        self,
        models: list[ChatModelBase],
        max_retries: int = 3,
        retry_delay: float = 1.0,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> None:
        super().__init__("resilient", stream=False)
        self.models = models
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout
        self._failed_models: set = set()

    async def __call__(self, messages, **kwargs):
        # 过滤掉已失败的模型
        available = [m for m in self.models if m not in self._failed_models]
        if not available:
            available = self.models  # 所有都失败时重置
            self._failed_models.clear()

        # 随机选择（简单负载均衡）
        model = random.choice(available)

        for attempt in range(self.max_retries):
            try:
                # 超时控制
                return await asyncio.wait_for(
                    model(messages, **kwargs),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                self._failed_models.add(model)
                available = [m for m in available if m != model]
                if not available:
                    break
                model = random.choice(available)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(self.retry_delay * (2 ** attempt))

        raise RuntimeError("所有模型均调用失败")
```

---

| 关联模块 | 关联点 | 参考位置 |
|----------|--------|----------|
| [消息模块](module_message_deep.md#3-核心类与函数源码解读) | Model 接收和返回 Msg 对象 | 第 3.1 节 |
| [格式化器模块](module_formatter_deep.md#3-源码解读) | Formatter 将 Msg 转换为 API 格式 | 第 3.1-3.4 节 |
| [工具模块](module_tool_mcp_deep.md#6-工具调用流程) | 工具调用机制与 Toolkit 协作 | 第 6.1-6.3 节 |
| [嵌入与 Token 模块](module_embedding_token_deep.md#8-token-计数机制) | Token 计数用于成本控制和截断 | 第 8.1-8.4 节 |
| [智能体模块](module_agent_deep.md#4-reactagent-实现类分析) | ReActAgent 通过 Model 进行推理 | 第 4.1-4.3 节 |
| [追踪模块](module_tracing_deep.md#3-追踪装饰器) | trace_llm() 追踪模型调用 | 第 3.1 节 |


---

## 参考资料

### 源码文件路径

| 文件 | 说明 |
|------|------|
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_model_base.py` | ChatModelBase 抽象基类 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_model_response.py` | ChatResponse 数据类 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_model_usage.py` | ChatUsage 使用统计类 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_openai_model.py` | OpenAI 适配器 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_dashscope_model.py` | DashScope 适配器 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_anthropic_model.py` | Anthropic 适配器 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_gemini_model.py` | Gemini 适配器 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_ollama_model.py` | Ollama 适配器 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/model/_trinity_model.py` | Trinity 多模型适配器 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/_utils/_common.py` | 通用工具函数 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/token/_token_base.py` | Token 计数器基类 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/token/_openai_token_counter.py` | OpenAI Token 计数器 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/embedding/_embedding_base.py` | Embedding 模型基类 |
| `/Users/nadav/IdeaProjects/agentscope/src/agentscope/embedding/_openai_embedding.py` | OpenAI Embedding 实现 |

### 关键类和方法索引

| 类/方法 | 位置 | 说明 |
|---------|------|------|
| `ChatModelBase` | `_model_base.py:13` | 模型适配器基类 |
| `ChatResponse` | `_model_response.py:19` | 响应数据类 |
| `ChatUsage` | `_model_usage.py:9` | 使用统计类 |
| `OpenAIChatModel.__call__` | `_openai_model.py:175` | OpenAI 模型调用入口 |
| `OpenAIChatModel._parse_openai_stream_response` | `_openai_model.py:343` | 流式响应解析 |
| `OpenAIChatModel._parse_openai_completion_response` | `_openai_model.py:558` | 非流式响应解析 |
| `DashScopeChatModel.__call__` | `_dashscope_model.py:162` | DashScope 模型调用入口 |
| `DashScopeChatModel._parse_dashscope_stream_response` | `_dashscope_model.py:300` | 流式响应解析 |
| `_json_loads_with_repair` | `_common.py:31` | JSON 修复与解析 |
| `_parse_streaming_json_dict` | `_common.py:72` | 流式 JSON 增量解析 |
| `_create_tool_from_base_model` | `_common.py:266` | 从 Pydantic 模型创建工具 |
| `OpenAITokenCounter` | `_openai_token_counter.py:297` | OpenAI Token 计数器 |
| `EmbeddingModelBase` | `_embedding_base.py:8` | Embedding 模型基类 |
| `OpenAITextEmbedding` | `_openai_embedding.py:13` | OpenAI Embedding 实现 |

---

*文档版本: 2.2*
*最后更新: 2026-04-28*

**本次更新 (2.2)**:
- 新增 10.0 章节：API Key 配置说明（环境变量、直接传入、Azure）
- 修复示例代码：补充缺失的 api_key 参数
- 所有示例添加 API Key 配置方式说明
