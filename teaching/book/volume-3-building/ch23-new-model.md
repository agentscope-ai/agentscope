# 第 23 章 造一个新 Model

> 本章你将：创建一个自定义模型适配器，接入新的 LLM 提供商。

---

## 23.1 目标

继承 `ChatModelBase`，创建一个自定义模型类。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 23.2 ChatModelBase 接口

继承 `ChatModelBase` 需要实现：

```python
from agentscope.model import ChatModelBase
from agentscope.model._model_response import ChatResponse

class MyCustomModel(ChatModelBase):
    def __init__(self, model_name: str, stream: bool = False, **kwargs):
        super().__init__(model_name=model_name, stream=stream)
        # 初始化你的 API 客户端
        self.client = ...

    async def __call__(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        # 调用你的 API
        # 解析响应为 ChatResponse
        ...
```

---

## 23.3 Step-by-Step

### Step 1: 创建文件

在 `src/agentscope/model/` 下创建 `_my_custom_model.py`。

### Step 2: 实现非流式调用

```python
async def __call__(self, messages, tools=None, **kwargs):
    # 1. 调用 API
    raw_response = await self.client.chat(messages=messages, tools=tools)

    # 2. 解析为 Msg
    msg = Msg("assistant", raw_response.text, "assistant")

    # 3. 包装为 ChatResponse
    return ChatResponse(
        message=msg,
        usage=ChatUsage(
            prompt_tokens=raw_response.usage.input,
            completion_tokens=raw_response.usage.output,
        ),
    )
```

### Step 3: 实现流式调用

```python
async def __call__(self, messages, tools=None, **kwargs):
    if not self.stream:
        return await self._call_non_stream(messages, tools)

    # 流式
    async def stream_generator():
        async for chunk in self.client.chat_stream(messages=messages):
            msg = Msg("assistant", chunk.delta, "assistant")
            yield ChatResponse(message=msg, usage=None)

    return stream_generator()
```

### Step 4: 注册到 `__init__.py`

在 `src/agentscope/model/__init__.py` 中添加：

```python
from ._my_custom_model import MyCustomModel

__all__ = [
    ...,
    "MyCustomModel",
]
```

### Step 5: 使用

```python
model = MyCustomModel(model_name="my-model", stream=True)
```

---

## 23.4 其他提供商的参考实现

查看现有实现作为参考：

| 提供商 | 文件 |
|--------|------|
| OpenAI | `_openai_model.py` |
| Anthropic | `_anthropic_model.py` |
| Gemini | `_gemini_model.py` |
| DashScope | `_dashscope_model.py` |
| Ollama | `_ollama_model.py` |

---

## 23.5 试一试

1. 创建一个 mock model（不调用真实 API，返回固定响应）
2. 让它支持 Tool Calling
3. 测试流式输出

---

## 23.6 检查点

你现在已经能：

- 继承 `ChatModelBase` 创建自定义模型
- 实现流式和非流式调用
- 将模型注册到框架

---

## 下一章预告
