# 给 agentscope chat 接口增加 custom_params（供中间件使用）的方案

> 会话日期：2026-08-04
> 目标：在尽量不动 agentscope 源码的前提下，为 `POST /chat/` 接口增加自定义参数 `custom_params`，并让这些参数能被 Agent 业务中间件（`MiddlewareBase`）读到。

## 一、关键结论

- `ChatRequest` schema 只接受 `agent_id` / `session_id` / `input` 三个字段。
- `extra_agent_middlewares` 工厂签名固定为 `(user_id, agent_id, session_id) -> list[MiddlewareBase]`，**不包含请求级数据**。
- 因此框架本身**没有为"单次请求的 custom_params"预留传递通道**，需要从外部注入。
- chat 路由是 fire-and-forget：`chat_run_registry.spawn(chat_service.run(...))`，请求返回后由后台任务执行。

## 二、涉及的关键源码位置

| 位置 | 说明 |
| --- | --- |
| `src/agentscope/app/_router/_schema/_chat.py` | `ChatRequest` / `ChatTriggerResponse` 定义 |
| `src/agentscope/app/_router/_chat.py` | `POST /chat/` handler，fire-and-forget spawn |
| `src/agentscope/app/_types.py` | `AgentMiddlewareFactory` 类型别名 |
| `src/agentscope/app/_service/_chat.py` | `ChatService.run` / `_run_impl`，第 2 步调用 `extra_agent_middlewares` |
| `src/agentscope/app/_app.py` | `create_app(...)` 入口，接收 `extra_agent_middlewares` / `extra_middlewares` |
| `src/agentscope/app/_lifespan.py` | 构造 `ChatService`，注入 `app.state.extra_agent_middlewares` |
| `src/agentscope/middleware/_base.py` | `MiddlewareBase` 各 hook 定义 |
| `src/agentscope/message/_base.py` | `Msg` 定义，含 `metadata: dict` 字段 |
| `src/agentscope/app/_manager/_chat_run_registry.py` | `spawn` 用 `asyncio.create_task`（会复制 context） |

## 三、三种实现方案

### 方案 A：`contextvars` + ASGI 中间件（零源码改动，推荐）

原理：在 ASGI 中间件中解析请求体，把 `custom_params` 写入 `contextvars.ContextVar`；因为 `spawn` 用的是 `asyncio.create_task`（会复制当前 context），chat run 后台任务中的 `extra_agent_middlewares` 工厂及中间件 hook 均能读到。

```python
# your_app/custom_params.py
import contextvars
import json
from typing import Any
from starlette.types import ASGIApp, Receive, Scope, Send

custom_params_var: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "agentscope_custom_params", default={},
)


class CustomParamsMiddleware:
    """ASGI 中间件：从 chat 请求体提取 custom_params 写入 ContextVar。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" \
                or not scope["path"].rstrip("/").endswith("/chat"):
            await self.app(scope, receive, send)
            return

        # 先把 body 全部读完
        body = b""
        more = True
        while more:
            msg = await receive()
            if msg["type"] == "http.request":
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
            else:
                break

        try:
            payload = json.loads(body) if body else {}
            custom_params_var.set(payload.get("custom_params", {}) or {})
        except Exception:
            payload = {}

        # 重建一次性 receive，把 body 还给下游
        replayed = False

        async def replay_receive() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)
```

自定义中间件读取：

```python
from agentscope.middleware import MiddlewareBase
from your_app.custom_params import custom_params_var

class MyCustomMiddleware(MiddlewareBase):
    async def on_reply(self, agent, input_kwargs, next_handler):
        params = custom_params_var.get()
        # 用 params 做事 ...
        async for event in next_handler():
            yield event
```

接入 `create_app`：

```python
from fastapi.middleware import Middleware
from agentscope.app import create_app
from your_app.custom_params import CustomParamsMiddleware
from your_app.middlewares import MyCustomMiddleware

async def extra_agent_middlewares_factory(user_id, agent_id, session_id):
    return [MyCustomMiddleware()]

app = create_app(
    storage=...,
    message_bus=...,
    workspace_manager=...,
    extra_middlewares=[Middleware(CustomParamsMiddleware)],  # ASGI 层
    extra_agent_middlewares=extra_agent_middlewares_factory,  # agent 层
)
```

优点：完全不动 agentscope 源码。
缺点：`custom_params` 不会出现在 OpenAPI 文档中；极端异步边界下 ContextVar 理论上有丢失风险（实践少见）。

### 方案 B：把 custom_params 塞进 `Msg.metadata`（零源码改动，最简）

适用于 `input` 为 `Msg` / `list[Msg]` 的场景。客户端直接把 `custom_params` 放进最后一条消息的 `metadata`：

```json
POST /chat/
{
  "agent_id": "...",
  "session_id": "...",
  "input": {
    "name": "user",
    "role": "user",
    "content": [{"type": "text", "text": "你好"}],
    "metadata": {"custom_params": {"foo": "bar"}}
  }
}
```

中间件读取：

```python
class MyCustomMiddleware(MiddlewareBase):
    async def on_reply(self, agent, input_kwargs, next_handler):
        inputs = input_kwargs.get("inputs")
        if isinstance(inputs, Msg) and inputs.metadata:
            params = inputs.metadata.get("custom_params", {})
        elif isinstance(inputs, list) and inputs:
            params = inputs[-1].metadata.get("custom_params", {})
        else:
            params = {}
        async for event in next_handler():
            yield event
```

优点：极简；`Msg.metadata` 本就是附加数据的合理位置。
缺点：仅对 `Msg`/`list[Msg]` 有效（对 `None` / HITL 事件无效）；`metadata` 会被持久化到 storage，敏感数据需注意。

### 方案 C：改一行源码 —— `ChatRequest` 加字段（最规范）

在 `ChatRequest` 中新增 `custom_params` 字段，前端有完整的 OpenAPI 契约；数据传递仍借助方案 A 的 ContextVar 或闭包。

```python
class ChatRequest(BaseModel):
    agent_id: str = Field(...)
    session_id: str = Field(...)
    input: (...) = Field(...)
    custom_params: dict = Field(default_factory=dict, description="自定义参数")
```

优点：契约完整、语义清晰。
缺点：改了源码，升级 agentscope 需要 rebase。

## 四、建议

- 仅给单条用户消息附加参数、量不大 → **方案 B**（`Msg.metadata`）。
- 需覆盖所有触发路径（HITL resume、wakeup）→ **方案 A**（ASGI 中间件 + ContextVar）。
- 需要前端清晰的 OpenAPI 契约 → **方案 C**（改 schema + 方案 A 的传递机制）。

## 五、附注

- `ChatRunRegistry.spawn` 使用 `asyncio.create_task`，官方保证复制当前 context，因此方案 A 的 ContextVar 能可靠传递到后台任务。
- `extra_agent_middlewares` 在 `_service/_chat.py` 第 2 步被 `await` 调用，是 agent 中间件注入的官方扩展点。
