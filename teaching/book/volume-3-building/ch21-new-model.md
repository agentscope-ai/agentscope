# 第二十一章：造一个新 Model Provider——从非流式到结构化输出

**难度**：中级

> 第九章我们追踪了 `ChatModelBase` 的抽象接口和 `FormatterBase` 的消息转换流程。本章你要亲自动手——为一家虚构的 FastLLM API 实现完整的 Model Provider。从非流式最小版本开始，逐步加上流式响应和结构化输出，最后接入 ReActAgent 做集成测试。

---

## 1. 实战目标

完成本章后，你将：

1. 继承 `ChatModelBase`，实现非流式和流式调用
2. 编写匹配的 `Formatter`，复用 OpenAI 格式
3. 用 `_create_tool_from_base_model` 实现结构化输出的 tool-call 回退
4. 与 ReActAgent 完成端到端集成

---

## 2. 第一步：最小可用版本

### 2.1 两个基类的契约

打开 `src/agentscope/model/_model_base.py`，核心抽象只有两处：

```python
# _model_base.py 第 13-44 行
class ChatModelBase:
    model_name: str
    stream: bool

    def __init__(self, model_name: str, stream: bool) -> None: ...

    @abstractmethod
    async def __call__(
        self, *args, **kwargs,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        pass
```

三个要点：

1. **构造函数**必须调用 `super().__init__(model_name, stream)`
2. **`__call__`** 是唯一抽象方法，返回 `ChatResponse` 或 `AsyncGenerator`
3. 基类提供 `_validate_tool_choice`（第 46-77 行）校验 tool_choice

`ChatResponse`（`_model_response.py` 第 19-42 行）的 `content` 存放 `TextBlock | ToolUseBlock | ThinkingBlock | AudioBlock` 序列，`ChatUsage`（`_model_usage.py` 第 9-25 行）记录 token 用量和耗时。

### 2.2 FastLLM 假设

- 基础 URL `https://api.fastllm.example/v1`，兼容 OpenAI 协议
- 不支持 `response_format`（结构化输出需走 tool-call 回退）
- 流式 SSE 格式与 OpenAI 一致，不支持 thinking 块

### 2.3 最小非流式 Model

创建 `src/agentscope/model/_fastllm_model.py`：

```python
# -*- coding: utf-8 -*-
"""FastLLM chat model class."""
from datetime import datetime
from typing import Any, AsyncGenerator, List, Literal, Type
from collections import OrderedDict
from pydantic import BaseModel

from . import ChatResponse
from ._model_base import ChatModelBase
from ._model_usage import ChatUsage
from .._logging import logger
from .._utils._common import _json_loads_with_repair, _create_tool_from_base_model
from ..message import TextBlock, ToolUseBlock
from ..tracing import trace_llm
from ..types import JSONSerializableObject


class FastLLMChatModel(ChatModelBase):
    """The FastLLM chat model class."""

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        stream: bool = False,
        base_url: str = "https://api.fastllm.example/v1",
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model_name, stream)
        import openai
        self.client = openai.AsyncClient(
            api_key=api_key, base_url=base_url,
            **(client_kwargs or {}),
        )
        self.generate_kwargs = generate_kwargs or {}

    @trace_llm
    async def __call__(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: Literal["auto", "none", "required"] | str | None = None,
        structured_model: Type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        kwargs = {
            "model": self.model_name,
            "messages": messages,
            **self.generate_kwargs, **kwargs,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            self._validate_tool_choice(tool_choice, tools)
            kwargs["tool_choice"] = tool_choice

        start_datetime = datetime.now()

        if structured_model:
            if tools or tool_choice:
                logger.warning("structured_model overrides tools/tool_choice.")
            kwargs.pop("tools", None)
            kwargs.pop("tool_choice", None)
            return await self._structured_via_tool_call(
                kwargs, structured_model, start_datetime,
            )

        if self.stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}

        response = await self.client.chat.completions.create(**kwargs)
        if self.stream:
            return self._parse_stream_response(start_datetime, response)
        return self._parse_completion_response(start_datetime, response)

    def _parse_completion_response(
        self, start_datetime: datetime, response: Any,
    ) -> ChatResponse:
        content_blocks: List[TextBlock | ToolUseBlock] = []
        if response.choices:
            choice = response.choices[0]
            if choice.message.content:
                content_blocks.append(
                    TextBlock(type="text", text=choice.message.content),
                )
            for tc in choice.message.tool_calls or []:
                content_blocks.append(ToolUseBlock(
                    type="tool_use", id=tc.id, name=tc.function.name,
                    input=_json_loads_with_repair(tc.function.arguments),
                ))
        usage = None
        if response.usage:
            usage = ChatUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                time=(datetime.now() - start_datetime).total_seconds(),
            )
        return ChatResponse(
            content=content_blocks, usage=usage,
            id=getattr(response, "id", None) or "",
        )

    def _format_tools_json_schemas(self, schemas: list[dict]) -> list[dict]:
        return schemas  # FastLLM schema 格式与 OpenAI 一致

    async def _structured_via_tool_call(
        self, kwargs: dict, structured_model: Type[BaseModel],
        start_datetime: datetime,
    ) -> ChatResponse:
        format_tool = _create_tool_from_base_model(structured_model)
        kwargs["tools"] = self._format_tools_json_schemas([format_tool])
        kwargs["tool_choice"] = {
            "type": "function",
            "function": {"name": format_tool["function"]["name"]},
        }
        response = await self.client.chat.completions.create(**kwargs)
        parsed = self._parse_completion_response(start_datetime, response)
        metadata = None
        for block in parsed.content:
            if block.get("type") == "tool_use":
                metadata = block.get("input")
        return ChatResponse(
            content=parsed.content, usage=parsed.usage,
            id=parsed.id, metadata=metadata,
        )
```

设计要点：

- 复用 `openai.AsyncClient` + 改 `base_url`：OpenAI 兼容 API 的标准做法
- 结构化输出始终走 `_structured_via_tool_call`
- `_format_tools_json_schemas` 直通返回；schema 格式不同时在此转换

---

## 3. 第二步：注册并测试

### 3.1 编写 Formatter

创建 `src/agentscope/formatter/_fastllm_formatter.py`，继承 `OpenAIChatFormatter`。由于 FastLLM 不支持图片，设 `support_vision = False`：

```python
# -*- coding: utf-8 -*-
"""The FastLLM formatter module."""
from ._openai_formatter import OpenAIChatFormatter, OpenAIMultiAgentFormatter
from ..message import TextBlock, ToolUseBlock, ToolResultBlock
from ..token import TokenCounterBase


class FastLLMChatFormatter(OpenAIChatFormatter):
    """FastLLM formatter — inherits OpenAI format, disables vision."""
    support_vision: bool = False
    supported_blocks: list[type] = [TextBlock, ToolUseBlock, ToolResultBlock]

    def __init__(self, token_counter=None, max_tokens=None) -> None:
        super().__init__(promote_tool_result_images=False,
                         token_counter=token_counter, max_tokens=max_tokens)


class FastLLMMultiAgentFormatter(OpenAIMultiAgentFormatter):
    """FastLLM multi-agent formatter."""
    support_vision: bool = False
    supported_blocks: list[type] = [TextBlock, ToolUseBlock, ToolResultBlock]
```

### 3.2 注册到 `__init__.py`

在 `model/__init__.py` 添加 `from ._fastllm_model import FastLLMChatModel`，在 `formatter/__init__.py` 添加 `from ._fastllm_formatter import FastLLMChatFormatter, FastLLMMultiAgentFormatter`。

### 3.3 单元测试

创建 `tests/fastllm_model_test.py`：

```python
# -*- coding: utf-8 -*-
"""Test the FastLLM model and formatter."""
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock
from pydantic import BaseModel

from agentscope.message import Msg, TextBlock, ToolUseBlock
from agentscope.model import ChatResponse
from agentscope.model._fastllm_model import FastLLMChatModel
from agentscope.formatter._fastllm_formatter import FastLLMChatFormatter


def _mock_resp(content="Hello!", tool_calls=None):
    msg = MagicMock(content=content, tool_calls=tool_calls)
    return MagicMock(
        choices=[MagicMock(message=msg)], id="fastllm-123",
        usage=MagicMock(prompt_tokens=10, completion_tokens=5),
    )


class FastLLMModelTest(IsolatedAsyncioTestCase):
    async def test_non_streaming(self) -> None:
        model = FastLLMChatModel(model_name="fastllm-v1", api_key="k")
        model.client = MagicMock()
        model.client.chat.completions.create = AsyncMock(
            return_value=_mock_resp("Hi from FastLLM!"),
        )
        result = await model(messages=[{"role": "user", "content": "Hi"}])
        self.assertIsInstance(result, ChatResponse)
        self.assertEqual(result.content[0]["text"], "Hi from FastLLM!")

    async def test_tool_call(self) -> None:
        tc = MagicMock(id="c1")
        tc.function.name = "get_weather"
        tc.function.arguments = '{"city": "Beijing"}'
        model = FastLLMChatModel(model_name="fastllm-v1", api_key="k")
        model.client = MagicMock()
        model.client.chat.completions.create = AsyncMock(
            return_value=_mock_resp(content=None, tool_calls=[tc]),
        )
        result = await model(
            messages=[{"role": "user", "content": "Weather?"}],
            tools=[{"type": "function", "function": {
                "name": "get_weather",
                "parameters": {"type": "object",
                    "properties": {"city": {"type": "string"}}},
            }}],
        )
        self.assertEqual(result.content[0]["name"], "get_weather")
        self.assertEqual(result.content[0]["input"]["city"], "Beijing")


class FastLLMFormatterTest(IsolatedAsyncioTestCase):
    async def test_format_basic(self) -> None:
        fmt = FastLLMChatFormatter()
        result = await fmt.format([
            Msg(name="system", content="Be helpful.", role="system"),
            Msg(name="user", content="Hello", role="user"),
        ])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["role"], "system")
```

---

## 4. 第三步：流式响应

### 4.1 流式协议

AgentScope 流式约定：`self.stream = True` 时返回 `AsyncGenerator[ChatResponse, None]`，每次 yield 包含**累积内容**而非增量。参考 `_openai_model.py` 第 346-559 行：

```python
text = ""
async for item in stream:
    text += item.choices[0].delta.content or ""
    yield ChatResponse(content=[TextBlock(type="text", text=text)], ...)
```

### 4.2 实现流式解析

在 `FastLLMChatModel` 中添加。核心逻辑与 `OpenAIChatModel._parse_openai_stream_response`（`_openai_model.py` 第 346-559 行）同构：

```python
async def _parse_stream_response(
    self, start_datetime: datetime, response: Any,
) -> AsyncGenerator[ChatResponse, None]:
    usage, text, tool_calls, response_id = None, "", OrderedDict(), None

    async with response as stream:
        async for chunk in stream:
            if response_id is None:
                response_id = getattr(chunk, "id", None)
            if chunk.usage:
                usage = ChatUsage(
                    input_tokens=chunk.usage.prompt_tokens,
                    output_tokens=chunk.usage.completion_tokens,
                    time=(datetime.now() - start_datetime).total_seconds(),
                )

            # usage-only final chunk
            if not chunk.choices:
                if usage:
                    yield ChatResponse(
                        content=self._build_contents(text, tool_calls),
                        usage=usage, id=response_id or "",
                    )
                continue

            choice = chunk.choices[0]
            text += getattr(choice.delta, "content", None) or ""

            # 累积 tool call 片段
            for tc in getattr(choice.delta, "tool_calls", None) or []:
                if tc.index in tool_calls:
                    if tc.function.arguments is not None:
                        tool_calls[tc.index]["input"] += tc.function.arguments
                else:
                    tool_calls[tc.index] = {
                        "id": tc.id, "name": tc.function.name,
                        "input": tc.function.arguments or "",
                    }

            contents = self._build_contents(text, tool_calls)
            if contents:
                _kw: dict[str, Any] = {"content": contents, "usage": usage}
                if response_id:
                    _kw["id"] = response_id
                yield ChatResponse(**_kw)

def _build_contents(self, text, tool_calls):
    contents = []
    if text:
        contents.append(TextBlock(type="text", text=text))
    for tc in tool_calls.values():
        contents.append(ToolUseBlock(
            type="tool_use", id=tc["id"], name=tc["name"],
            input=_json_loads_with_repair(tc["input"]),
        ))
    return contents
```

### 4.3 流式测试

```python
async def test_streaming(self) -> None:
    # 构造三个文本 chunk + 一个 usage-only final chunk
    chunks = []
    for token in ["Hello", " from", " FastLLM"]:
        c = MagicMock(id="s-1", usage=None, choices=[MagicMock()])
        c.choices[0].delta.content = token
        c.choices[0].delta.tool_calls = None
        chunks.append(c)
    final = MagicMock(id="s-1", choices=[])
    final.usage = MagicMock(prompt_tokens=10, completion_tokens=3)
    chunks.append(final)

    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=chunks.__aiter__())
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    mock_stream.__aiter__ = MagicMock(return_value=chunks.__aiter__())

    model = FastLLMChatModel(model_name="fastllm-v1", api_key="k", stream=True)
    model.client = MagicMock()
    model.client.chat.completions.create = AsyncMock(return_value=mock_stream)

    result = await model(messages=[{"role": "user", "content": "Hi"}])
    collected = [c async for c in result]  # type: ignore[union-attr]
    # 最后一个 chunk 包含累积的完整文本
    self.assertIn("FastLLM", collected[-1].content[0]["text"])
```

---

## 5. 第四步：结构化输出

### 5.1 两种实现路径

`OpenAIChatModel`（`_openai_model.py` 第 271-325 行）支持两种策略：

1. **`response_format`**：API 原生返回 JSON（OpenAI 支持）
2. **Tool-call 回退**：用 `_create_tool_from_base_model`（`_utils/_common.py` 第 266-322 行）把 Pydantic model 转成工具函数

`_create_tool_from_base_model` 的流程：`model_json_schema()` -> 移除 `title` -> 包装成 `{"type": "function", "function": {"name": "generate_structured_output", "parameters": schema}}`。

FastLLM 不支持 `response_format`，始终走第二种。

### 5.2 测试结构化输出

```python
class WeatherInfo(BaseModel):
    city: str
    temperature: float
    condition: str

async def test_structured_output(self) -> None:
    tc = MagicMock(id="cs1")
    tc.function.name = "generate_structured_output"
    tc.function.arguments = json.dumps({
        "city": "Shanghai", "temperature": 22.5, "condition": "sunny",
    })
    model = FastLLMChatModel(model_name="fastllm-v1", api_key="k")
    model.client = MagicMock()
    model.client.chat.completions.create = AsyncMock(
        return_value=_mock_resp(content=None, tool_calls=[tc]),
    )
    result = await model(
        messages=[{"role": "user", "content": "Shanghai weather"}],
        structured_model=WeatherInfo,
    )
    self.assertEqual(result.metadata["city"], "Shanghai")
    self.assertAlmostEqual(result.metadata["temperature"], 22.5)
```

调用链路：`__call__` -> `_structured_via_tool_call` -> `_create_tool_from_base_model(WeatherInfo)` 生成工具名为 `"generate_structured_output"` -> `tool_choice` 强制调用 -> 从 `ToolUseBlock.input` 提取 `metadata`。

注意：`_create_tool_from_base_model` 默认工具名为 `"generate_structured_output"`（`_utils/_common.py` 第 268 行），必须与 `tool_choice` 中一致。

---

## 6. 第五步：集成测试

### 6.1 构造 ReActAgent

`ReActAgent.__init__`（`_react_agent.py` 第 177-276 行）接收 `model: ChatModelBase` 和 `formatter: FormatterBase`：

```python
from agentscope.agent import ReActAgent
from agentscope.formatter._fastllm_formatter import FastLLMChatFormatter
from agentscope.message import Msg
from agentscope.model._fastllm_model import FastLLMChatModel

model = FastLLMChatModel(model_name="fastllm-v1", api_key="test-key")
agent = ReActAgent(
    name="fastllm_agent",
    model=model,
    formatter=FastLLMChatFormatter(),
)

def greet(name: str) -> str:
    """Say hello to someone.

    Args:
        name (`str`): The person's name.
    """
    return f"Hello, {name}!"

agent.toolkit.register_tool_function(greet)
```

### 6.2 带 Mock 的端到端测试

```python
async def test_react_agent_with_fastllm(self) -> None:
    from agentscope.agent._react_agent import ReActAgent

    model = FastLLMChatModel(model_name="fastllm-v1", api_key="k")
    model.client = MagicMock()

    tc = MagicMock(id="c1")
    tc.function.name = "greet"
    tc.function.arguments = '{"name": "World"}'

    # 第一次调用返回工具调用，第二次返回最终文本
    model.client.chat.completions.create = AsyncMock(side_effect=[
        _mock_resp(content=None, tool_calls=[tc]),
        _mock_resp(content="I greeted the world!"),
    ])

    agent = ReActAgent(name="test_agent", model=model,
                       formatter=FastLLMChatFormatter())

    def greet(name: str) -> str:
        """Say hello.

        Args:
            name (`str`): The person's name.
        """
        return f"Hello, {name}!"

    agent.toolkit.register_tool_function(greet)
    result = await agent(Msg(name="user", content="Say hello to World"))
    self.assertIsNotNone(result)
```

---

## 7. PR 检查清单

- [ ] Model 继承 `ChatModelBase`，`__call__` 签名包含 `messages`、`tools`、`tool_choice`、`structured_model`
- [ ] 非流式返回 `ChatResponse`，流式返回 `AsyncGenerator[ChatResponse, None]`
- [ ] `ChatResponse.content` 使用标准 block（`TextBlock`、`ToolUseBlock`）；`ChatUsage` 正确记录 token 和耗时
- [ ] 结构化输出通过 `_create_tool_from_base_model` 实现 tool-call 回退
- [ ] Formatter 继承 `TruncatedFormatterBase`（或 `OpenAIChatFormatter`），在 `__init__.py` 中注册
- [ ] 第三方库在方法内部 lazy import，通过 `pytest` 和 `pre-commit run --all-files`

---

## 8. 下一章预告

下一章我们将造一个 Memory 后端——把对话历史存到 Redis，实现跨会话的记忆持久化。
