# Pipeline 与基础设施模块深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [Pipeline 工作流编排](#2-pipeline-工作流编排)
3. [Formatter 消息格式化](#3-formatter-消息格式化)
4. [Realtime 实时交互](#4-realtime-实时交互)
5. [Session 会话管理](#5-session-会话管理)
6. [Tracing 追踪系统](#6-tracing-追踪系统)
7. [A2A 协议](#7-a2a-协议)
8. [其他基础设施模块](#8-其他基础设施模块)
9. [代码示例](#9-代码示例)
10. [练习题](#10-练习题)

---

## 1. 模块概述

### 1.1 目录结构

```
src/agentscope/pipeline/
├── __init__.py
├── _msghub.py              # 消息中心
├── _chat_room.py           # 聊天室
├── _class.py               # Pipeline 类封装
└── _functional.py           # Pipeline 函数式封装

src/agentscope/formatter/
├── __init__.py
├── _formatter_base.py      # 格式化器基类
├── _openai_formatter.py    # OpenAI 格式化器
├── _dashscope_formatter.py # DashScope 格式化器
├── _anthropic_formatter.py # Anthropic 格式化器
├── _gemini_formatter.py    # Gemini 格式化器
├── _ollama_formatter.py    # Ollama 格式化器
├── _deepseek_formatter.py  # DeepSeek 格式化器
├── _truncated_formatter_base.py  # 截断格式化器
└── _a2a_formatter.py       # A2A 格式化器

src/agentscope/realtime/
├── __init__.py
├── _websocket.py           # WebSocket 实现
└── ...

src/agentscope/session/
├── __init__.py
├── _session.py            # 会话管理
└── ...

src/agentscope/tracing/
├── __init__.py
├── _trace.py              # 追踪核心
└── ...

src/agentscope/tts/
├── __init__.py
├── _tts_base.py          # TTS 基类
└── ...

src/agentscope/a2a/
├── __init__.py
├── _agent_card.py        # Agent 卡
├── _protocol.py          # A2A 协议
└── ...
```

---

## 2. Pipeline 工作流编排

### 2.1 消息中心 MsgHub

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/_msghub.py`

MsgHub 是 AgentScope 的消息路由中心，支持多代理间的消息传递:

```python
class MsgHub:
    """Message hub for multi-agent communication."""

    def __init__(
        self,
        name: str,
        announcement: str | None = None,
    ) -> None:
        """Initialize the message hub.

        Args:
            name: Unique name for this hub
            announcement: Optional announcement message
        """
        self.name = name
        self.announcement = announcement
        self._agents: list[AgentBase] = []
        self._strategy: Callable = broadcast_strategy

    def add(self, agent: AgentBase) -> None:
        """Add an agent to this hub."""
        self._agents.append(agent)
        agent.reset_subscribers(self.name, self._agents)

    def remove(self, agent: AgentBase) -> None:
        """Remove an agent from this hub."""
        if agent in self._agents:
            self._agents.remove(agent)
            agent.remove_subscribers(self.name)

    async def broadcast(self, msg: Msg) -> None:
        """Broadcast a message to all agents in the hub."""
        for agent in self._agents:
            await agent.observe(msg)

    def set_strategy(self, strategy: Callable) -> None:
        """Set the message routing strategy."""
        self._strategy = strategy
```

### 2.2 Pipeline 类型

#### SequentialPipeline

顺序执行，上一个代理的输出作为下一个代理的输入:

```
UserInput -> Agent1 -> Agent2 -> Agent3 -> FinalOutput
```

```python
class SequentialPipeline:
    """Pipeline that executes agents sequentially."""

    def __init__(self, agents: list[AgentBase]) -> None:
        self.agents = agents

    async def run(self, initial_input: Msg) -> Msg:
        """Run the pipeline with initial input."""
        current = initial_input

        for agent in self.agents:
            result = await agent(current)
            current = result

        return current
```

#### ForkedPipeline

分支执行，多个代理并行处理相同输入:

```
                    -> Agent1 ->
UserInput -> Splitter            -> Aggregator -> Output
                    -> Agent2 ->
                    -> Agent3 ->
```

```python
class ForkedPipeline:
    """Pipeline that forks execution to multiple agents."""

    def __init__(
        self,
        agents: list[AgentBase],
        aggregator: Callable[[list[Msg]], Msg],
    ) -> None:
        self.agents = agents
        self.aggregator = aggregator

    async def run(self, initial_input: Msg) -> Msg:
        """Run the pipeline with parallel execution."""
        tasks = [agent(initial_input) for agent in self.agents]
        results = await asyncio.gather(*tasks)
        return self.aggregator(results)
```

#### WhileLoopPipeline

循环执行，直到满足退出条件:

```
                    +-------+
UserInput -> Agent1 +->cond?+--yes--> Agent1 (again)
                  |            |
                  +----no------+
                      |
                      v
                   Output
```

```python
class WhileLoopPipeline:
    """Pipeline that loops while a condition is met."""

    def __init__(
        self,
        agent: AgentBase,
        condition: Callable[[Msg], bool],
        max_iters: int = 10,
    ) -> None:
        self.agent = agent
        self.condition = condition
        self.max_iters = max_iters

    async def run(self, initial_input: Msg) -> Msg:
        """Run the pipeline with loop."""
        current = initial_input

        for _ in range(self.max_iters):
            if not self.condition(current):
                break
            current = await self.agent(current)

        return current
```

### 2.3 Pipeline 函数式接口

**文件**: `_functional.py`

```python
async def sequential(
    agents: list[AgentBase],
    initial_input: Msg,
) -> Msg:
    """Execute agents sequentially."""
    current = initial_input
    for agent in agents:
        current = await agent(current)
    return current

async def parallel(
    agents: list[AgentBase],
    initial_input: Msg,
) -> list[Msg]:
    """Execute agents in parallel."""
    tasks = [agent(initial_input) for agent in agents]
    return await asyncio.gather(*tasks)

async def pipeline_if(
    condition: Callable[[Msg], bool],
    then_agent: AgentBase,
    else_agent: AgentBase,
    input_msg: Msg,
) -> Msg:
    """Execute different agents based on condition."""
    if condition(input_msg):
        return await then_agent(input_msg)
    else:
        return await else_agent(input_msg)
```

---

## 3. Formatter 消息格式化

### 3.1 FormatterBase 基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/formatter/_formatter_base.py`

```python
class FormatterBase(ABC):
    """Base class for message formatters."""

    @abstractmethod
    async def format(
        self,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> list[dict]:
        """Format messages into provider-specific format.

        Args:
            msgs: List of Msg objects to format

        Returns:
            List of formatted message dictionaries
        """
        pass

    @abstractmethod
    def parse(
        self,
        response: ChatResponse,
    ) -> Msg:
        """Parse model response into Msg object."""
        pass
```

### 3.2 OpenAI Formatter

**文件**: `_openai_formatter.py`

```python
class OpenAIFormatter(FormatterBase):
    """Formatter for OpenAI API messages."""

    async def format(
        self,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> list[dict]:
        """Format messages for OpenAI API.

        Converts AgentScope Msg objects to OpenAI message format:
        {
            "role": "user" | "assistant" | "system",
            "content": str | list[...],
            "name": str (optional)
        }
        """
        formatted = []

        for msg in msgs:
            if msg.role == "system":
                content = msg.content
            elif isinstance(msg.content, str):
                content = msg.content
            else:
                # 处理多模态内容块
                content = self._format_content_blocks(msg.content)

            formatted.append({
                "role": msg.role,
                "content": content,
                "name": getattr(msg, "name", None),
            })

        return formatted

    def _format_content_blocks(
        self,
        blocks: list[dict],
    ) -> list[dict]:
        """Format content blocks for OpenAI."""
        formatted_blocks = []

        for block in blocks:
            if block["type"] == "text":
                formatted_blocks.append({
                    "type": "text",
                    "text": block["text"],
                })
            elif block["type"] == "image":
                formatted_blocks.append({
                    "type": "image_url",
                    "image_url": block["source"],
                })
            # ... 其他块类型

        return formatted_blocks
```

### 3.3 DashScope Formatter

**文件**: `_dashscope_formatter.py`

```python
class DashScopeFormatter(FormatterBase):
    """Formatter for DashScope API messages."""

    async def format(
        self,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> list[dict]:
        """Format messages for DashScope API.

        DashScope 使用与 OpenAI 相似的格式，但有一些差异:
        - 支持 Qwen 的特定内容块类型
        - 多模态内容格式不同
        """
        formatted = []

        for msg in msgs:
            formatted.append({
                "role": msg.role,
                "content": self._format_content(msg),
            })

        return formatted

    def _format_content(self, msg: Msg) -> str | list[dict]:
        """Format message content for DashScope."""
        if isinstance(msg.content, str):
            return msg.content

        # 处理内容块
        formatted_content = []
        for block in msg.content:
            if block["type"] == "text":
                formatted_content.append({
                    "text": block["text"],
                })
            elif block["type"] == "image":
                formatted_content.append({
                    "image": block["source"]["url"],
                })

        return formatted_content
```

### 3.4 Anthropic Formatter

**文件**: `_anthropic_formatter.py`

```python
class AnthropicFormatter(FormatterBase):
    """Formatter for Anthropic API messages."""

    async def format(
        self,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> list[dict]:
        """Format messages for Anthropic API.

        Anthropic 消息格式与 OpenAI 类似，但:
        - 使用 "user" 和 "assistant" 角色
        - 支持 system 消息的特殊处理
        - 支持 thinking 块
        """
        formatted = []

        for msg in msgs:
            if msg.role == "system":
                formatted.append({
                    "role": "user",
                    "content": f"<system>{msg.content}</system>",
                })
            else:
                formatted.append({
                    "role": msg.role,
                    "content": msg.content,
                })

        return formatted
```

### 3.5 截断格式化器

**文件**: `_truncated_formatter_base.py`

用于处理超出模型上下文窗口的过长消息:

```python
class TruncatedFormatterBase(FormatterBase):
    """Formatter with automatic truncation for long contexts."""

    def __init__(
        self,
        inner: FormatterBase,
        max_tokens: int = 100000,
        token_counter: TokenCounterBase,
    ) -> None:
        """Initialize with an inner formatter.

        Args:
            inner: The actual formatter to use
            max_tokens: Maximum tokens allowed
            token_counter: Token counter for truncation
        """
        self.inner = inner
        self.max_tokens = max_tokens
        self.token_counter = token_counter

    async def format(
        self,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> list[dict]:
        """Format messages with automatic truncation."""
        formatted = await self.inner.format(msgs)

        # 计算总 token 数
        total_tokens = await self.token_counter.count(formatted)

        # 如果超过限制，从最早的消息开始截断
        while total_tokens > self.max_tokens and len(formatted) > 1:
            removed = formatted.pop(0)
            total_tokens -= await self.token_counter.count([removed])

        return formatted
```

---

## 4. Realtime 实时交互

### 4.1 WebSocket 支持

AgentScope 支持通过 WebSocket 进行实时双向通信:

```python
class RealtimeConnection:
    """Represents a WebSocket connection."""

    def __init__(
        self,
        websocket: WebSocket,
        agent: AgentBase,
    ) -> None:
        self.websocket = websocket
        self.agent = agent
        self._receive_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start handling messages."""
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        """Receive and process messages."""
        async for message in self.websocket:
            msg = parse_realtime_message(message)
            response = await self.agent(msg)
            await self.websocket.send(response.to_json())

    async def send(self, message: dict) -> None:
        """Send a message to the client."""
        await self.websocket.send(json.dumps(message))

    async def close(self) -> None:
        """Close the connection."""
        if self._receive_task:
            self._receive_task.cancel()
        await self.websocket.close()
```

### 4.2 流式响应处理

```python
async def handle_streaming_response(
    websocket: WebSocket,
    response_stream: AsyncGenerator[ChatResponse, None],
) -> None:
    """Handle streaming responses via WebSocket."""
    async for chunk in response_stream:
        # 发送增量更新
        await websocket.send_json({
            "type": "content_delta",
            "delta": chunk.content,
        })

    # 发送完成信号
    await websocket.send_json({
        "type": "complete",
    })
```

---

## 5. Session 会话管理

### 5.1 Session 基类

**文件**: `src/agentscope/session/_session.py`

```python
class SessionBase(ABC):
    """Base class for session management."""

    @abstractmethod
    async def create(
        self,
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Create a new session.

        Returns:
            The session ID
        """

    @abstractmethod
    async def get(
        self,
        session_id: str,
    ) -> Session | None:
        """Get a session by ID."""

    @abstractmethod
    async def update(
        self,
        session_id: str,
        state: dict,
    ) -> None:
        """Update session state."""

    @abstractmethod
    async def delete(
        self,
        session_id: str,
    ) -> None:
        """Delete a session."""
```

### 5.2 Session 类

```python
class Session:
    """Represents a single session."""

    def __init__(
        self,
        session_id: str,
        created_at: float,
        metadata: dict,
        state: dict,
    ) -> None:
        self.session_id = session_id
        self.created_at = created_at
        self.metadata = metadata
        self.state = state

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
            "state": self.state,
        }
```

### 5.3 会话状态持久化

```python
class SQLiteSessionManager(SessionBase):
    """Session manager using SQLite."""

    def __init__(self, db_path: str) -> None:
        import aiosqlite
        self._conn = aiosqlite.connect(db_path)
        self._init_db()

    async def _init_db(self) -> None:
        """Initialize database schema."""
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at REAL,
                metadata TEXT,
                state TEXT
            )
        """)

    async def create(
        self,
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Create a new session."""
        session_id = session_id or str(uuid.uuid())
        await self._conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (session_id, time.time(), json.dumps(metadata), "{}"),
        )
        await self._conn.commit()
        return session_id
```

---

## 6. Tracing 追踪系统

### 6.1 追踪装饰器

**文件**: `src/agentscope/tracing/_trace.py`

AgentScope 提供函数级别的追踪:

```python
def trace_reply(func: Callable) -> Callable:
    """Decorator to trace agent reply function."""

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        # 开始追踪
        span_id = current_span().span_id
        start_time = time.time()

        try:
            # 执行原函数
            result = await func(self, *args, **kwargs)

            # 记录成功
            record_span(
                span_id=span_id,
                name=f"{self.__class__.__name__}.reply",
                start_time=start_time,
                end_time=time.time(),
                status="ok",
            )

            return result

        except Exception as e:
            # 记录错误
            record_span(
                span_id=span_id,
                name=f"{self.__class__.__name__}.reply",
                start_time=start_time,
                end_time=time.time(),
                status="error",
                error=str(e),
            )
            raise

    return wrapper

def trace_llm(func: Callable) -> Callable:
    """Decorator to trace LLM calls."""

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        start_time = time.time()
        model_name = getattr(self, "model_name", "unknown")

        try:
            result = await func(self, *args, **kwargs)

            # 记录 LLM 调用
            record_llm_call(
                model=model_name,
                prompt_tokens=result.usage.prompt_tokens if result.usage else 0,
                completion_tokens=result.usage.completion_tokens if result.usage else 0,
                start_time=start_time,
                end_time=time.time(),
            )

            return result

        except Exception as e:
            record_llm_call(
                model=model_name,
                error=str(e),
                start_time=start_time,
                end_time=time.time(),
            )
            raise

    return wrapper
```

### 6.2 追踪数据结构

```python
@dataclass
class Span:
    """Represents a trace span."""
    span_id: str
    parent_id: str | None
    name: str
    start_time: float
    end_time: float
    status: str  # "ok", "error"
    attributes: dict
    error: str | None

@dataclass
class LLMCall:
    """Represents an LLM API call."""
    model: str
    prompt_tokens: int
    completion_tokens: int
    start_time: float
    end_time: float
    error: str | None
```

### 6.3 追踪导出器

```python
class TracingExporter(ABC):
    """Base class for tracing exporters."""

    @abstractmethod
    def export(self, spans: list[Span]) -> None:
        """Export spans to storage."""
        pass

class ConsoleExporter(TracingExporter):
    """Export spans to console."""

    def export(self, spans: list[Span]) -> None:
        for span in spans:
            print(f"[{span.name}] {span.status} {span.end_time - span.start_time:.3f}s")

class OTLPExporter(TracingExporter):
    """Export spans to OTLP endpoint."""

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    def export(self, spans: list[Span]) -> None:
        # 发送到 OTLP 收集器
        payload = self._format_otlp_payload(spans)
        requests.post(self._endpoint, json=payload)
```

---

## 7. A2A 协议

### 7.1 A2A 协议概述

A2A (Agent-to-Agent) 协议定义了智能体之间通信的标准格式。

### 7.2 Agent Card

**文件**: `src/agentscope/a2a/_agent_card.py`

```python
class AgentCard:
    """Describes an agent's capabilities and endpoints."""

    def __init__(
        self,
        name: str,
        description: str,
        capabilities: list[str],
        endpoint: str,
        version: str = "1.0",
    ) -> None:
        self.name = name
        self.description = description
        self.capabilities = capabilities  # ["text", "code", "multimodal"]
        self.endpoint = endpoint
        self.version = version

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON."""
        return {
            "name": self.name,
            "description": self.description,
            "capabilities": self.capabilities,
            "endpoint": self.endpoint,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentCard":
        """Deserialize from dictionary."""
        return cls(**data)
```

### 7.3 A2A 消息格式

```python
class A2AMessage:
    """Standard A2A message format."""

    def __init__(
        self,
        message_id: str,
        sender: str,
        recipient: str | None,  # None for broadcast
        message_type: str,  # "request", "response", "event"
        payload: dict,
        timestamp: float | None = None,
    ) -> None:
        self.message_id = message_id
        self.sender = sender
        self.recipient = recipient
        self.message_type = message_type
        self.payload = payload
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "message_id": self.message_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "type": self.message_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }
```

### 7.4 A2A Client 实现

```python
class A2AClient:
    """Client for A2A protocol communication."""

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint
        self._session = httpx.AsyncClient()

    async def discover_agents(self) -> list[AgentCard]:
        """Discover available agents."""
        response = await self._session.get(f"{self._endpoint}/agents")
        return [AgentCard.from_dict(a) for a in response.json()]

    async def send_message(
        self,
        recipient: str,
        payload: dict,
    ) -> A2AMessage:
        """Send an A2A message to a recipient."""
        message = A2AMessage(
            message_id=str(uuid.uuid()),
            sender=self._endpoint,
            recipient=recipient,
            message_type="request",
            payload=payload,
        )

        response = await self._session.post(
            f"{self._endpoint}/messages",
            json=message.to_dict(),
        )

        return A2AMessage.from_dict(response.json())

    async def broadcast_event(
        self,
        event_type: str,
        payload: dict,
    ) -> None:
        """Broadcast an event to all agents."""
        message = A2AMessage(
            message_id=str(uuid.uuid()),
            sender=self._endpoint,
            recipient=None,  # broadcast
            message_type="event",
            payload={
                "event_type": event_type,
                **payload,
            },
        )

        await self._session.post(
            f"{self._endpoint}/broadcast",
            json=message.to_dict(),
        )
```

---

## 8. 其他基础设施模块

### 8.1 TTS 语音合成

**文件**: `src/agentscope/tts/`

```python
class TTSModelBase(ABC):
    """Base class for TTS models."""

    @abstractmethod
    async def synthesize(
        self,
        msg: Msg,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Synthesize speech from message."""
        pass

    @abstractmethod
    async def push(
        self,
        msg: Msg,
    ) -> ChatResponse:
        """Push message for streaming synthesis."""
        pass

    @property
    @abstractmethod
    def supports_streaming_input(self) -> bool:
        """Whether this TTS model supports streaming input."""
        pass
```

### 8.2 Module 状态管理

**文件**: `src/agentscope/module.py`

```python
class StateModule:
    """Base class for modules with state management."""

    def __init__(self) -> None:
        self._state_keys: set = set()

    def register_state(self, key: str) -> None:
        """Register a state key for serialization."""
        self._state_keys.add(key)

    def state_dict(self) -> dict:
        """Get the current state as a dictionary."""
        return {key: getattr(self, key, None) for key in self._state_keys}

    def load_state_dict(self, state_dict: dict) -> None:
        """Load state from a dictionary."""
        for key, value in state_dict.items():
            if key in self._state_keys:
                setattr(self, key, value)
```

### 8.3 Plan 模块

支持复杂任务分解:

```python
class PlanNotebook:
    """Manages task plans and sub-tasks."""

    def __init__(self) -> None:
        self._plans: list[Plan] = []

    def create_plan(
        self,
        task: str,
        subtasks: list[str],
    ) -> str:
        """Create a plan for a task."""
        plan = Plan(
            plan_id=str(uuid.uuid()),
            task=task,
            subtasks=[SubTask(id=str(uuid.uuid()), description=s) for s in subtasks],
        )
        self._plans.append(plan)
        return plan.plan_id

    async def get_current_hint(self) -> Msg | None:
        """Get hint for the current sub-task."""
        pass

    def mark_complete(self, subtask_id: str) -> None:
        """Mark a sub-task as complete."""
        pass
```

---

## 9. 代码示例

### 9.1 创建顺序 Pipeline

```python
from agentscope.pipeline import SequentialPipeline
from agentscope import AgentBase, Msg

# 创建代理
agent1 = MyAgent(name="agent1")
agent2 = MyAgent(name="agent2")
agent3 = MyAgent(name="agent3")

# 创建 Pipeline
pipeline = SequentialPipeline(agents=[agent1, agent2, agent3])

# 运行
initial = Msg(name="user", content="Start", role="user")
result = await pipeline.run(initial)
print(f"Final result: {result.content}")
```

### 9.2 创建并行 Pipeline

```python
from agentscope.pipeline import ForkedPipeline
import asyncio

# 创建分支代理
agents = [
    SearchAgent(name="searcher1"),
    SearchAgent(name="searcher2"),
    SearchAgent(name="searcher3"),
]

# 定义聚合函数
def aggregate_results(results: list[Msg]) -> Msg:
    combined = "\n".join([r.content for r in results])
    return Msg(name="aggregator", content=combined, role="assistant")

# 创建 Pipeline
pipeline = ForkedPipeline(agents=agents, aggregator=aggregate_results)

# 运行
initial = Msg(name="user", content="Search for AI news", role="user")
result = await pipeline.run(initial)
```

### 9.3 使用 Formatter

```python
from agentscope.formatter import OpenAIFormatter, DashScopeFormatter
from agentscope.message import Msg

# OpenAI Formatter
formatter = OpenAIFormatter()
messages = [
    Msg(name="system", content="You are helpful.", role="system"),
    Msg(name="user", content="Hello!", role="user"),
]

formatted = await formatter.format(messages)
print(formatted)
# [{'role': 'system', 'content': 'You are helpful.', 'name': 'system'},
#  {'role': 'user', 'content': 'Hello!', 'name': 'user'}]
```

### 9.4 会话管理

```python
from agentscope.session import SQLiteSessionManager

# 创建会话管理器
manager = SQLiteSessionManager("./sessions.db")

# 创建会话
session_id = await manager.create(
    metadata={"user_id": "user123", "topic": "support"},
)

# 获取并更新会话
session = await manager.get(session_id)
await manager.update(session_id, {"last_agent": "agent1"})

# 删除会话
await manager.delete(session_id)
```

### 9.5 A2A 通信

```python
from agentscope.a2a import A2AClient, AgentCard

# 创建 A2A 客户端
client = A2AClient("http://localhost:8000")

# 发现代理
agents = await client.discover_agents()
for agent in agents:
    print(f"{agent.name}: {agent.description}")

# 发送消息
response = await client.send_message(
    recipient="assistant-agent",
    payload={"query": "What is AI?"},
)

print(f"Response: {response.payload['answer']}")

# 广播事件
await client.broadcast_event(
    event_type="status_update",
    payload={"status": "available"},
)
```

---

## 10. 练习题

### 10.1 基础题

1. **分析 MsgHub 的消息广播机制，参考 `_msghub.py`。**

2. **比较三种 Pipeline 类型的适用场景。**

3. **解释 Formatter 在 AgentScope 中的作用。**

### 10.2 进阶题

4. **设计一个新的 Pipeline 类型，实现条件分支。**

5. **分析 Formatter 如何处理不同模型的消息格式差异。**

6. **设计一个支持断点恢复的 Pipeline。**

### 10.3 挑战题

7. **实现一个分布式 Pipeline，支持跨进程的代理协作。**

8. **分析 A2A 协议与 MCP 协议的异同，设计一个统一的代理通信框架。**

9. **设计一个 Pipeline 可视化工具，用于调试复杂的工作流。**

---

## 参考资料

- Pipeline 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/pipeline/`
- Formatter 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/formatter/`
- Realtime 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/realtime/`
- Session 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/session/`
- Tracing 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tracing/`
- A2A 源码: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/a2a/`

---

*文档版本: 1.0*
*最后更新: 2026-04-27*
