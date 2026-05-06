# 附录C：代码模板库

> **目标**：快速复制粘贴，让开发效率翻倍

---

## 1. Agent创建模板

### 基础Agent

```python showLineNumbers
import agentscope

# 初始化
agentscope.init(project="MyProject")

# 创建Agent
agent = agentscope.ReActAgent(
    name="Assistant",
    model=agentscope.OpenAIChatModel(
        api_key="your-api-key",
        model="gpt-4"
    ),
    sys_prompt="你是一个友好的助手",
    formatter=agentscope.OpenAIChatFormatter()
)

# 调用Agent
response = await agent("你好")
print(response)
```

### 带工具的Agent

```python showLineNumbers
from agentscope.tool import Tool, Toolkit

# 定义工具
@Tool
def calculate(expression: str) -> str:
    """计算数学表达式"""
    return str(eval(expression))

# 创建工具箱
toolkit = Toolkit([calculate])

# 创建带工具的Agent
agent = agentscope.ReActAgent(
    name="Assistant",
    model=agentscope.OpenAIChatModel(api_key="...", model="gpt-4"),
    sys_prompt="你是一个得力的助手",
    tools=toolkit
)
```

### 带记忆的Agent

```python showLineNumbers
from agentscope.memory import InMemoryMemory

# 创建记忆
memory = InMemoryMemory()

# 创建带记忆的Agent
agent = agentscope.ReActAgent(
    name="Assistant",
    model=agentscope.OpenAIChatModel(api_key="...", model="gpt-4"),
    sys_prompt="你是一个友好的助手",
    memory=memory
)
```

---

## 2. 消息创建模板

### 创建不同角色的Msg

```python showLineNumbers
from agentscope import Msg

# 用户消息
user_msg = Msg(name="user", content="你好", role="user")

# Agent回复
agent_msg = Msg(name="assistant", content="你好！", role="assistant")

# 系统消息
system_msg = Msg(name="system", content="欢迎使用", role="system")
```

---

## 3. Pipeline模板

### SequentialPipeline（顺序执行）

```python showLineNumbers
from agentscope.pipeline import SequentialPipeline

# 创建顺序Pipeline
pipeline = SequentialPipeline([agent_a, agent_b, agent_c])

# 执行
result = await pipeline(user_input)
```

### FanoutPipeline（并行执行）

```python showLineNumbers
from agentscope.pipeline import FanoutPipeline

# 创建并行Pipeline
pipeline = FanoutPipeline([agent_a, agent_b, agent_c])

# 执行，所有Agent同时处理
results = await pipeline(user_input)
```

---

## 4. MsgHub模板

### 发布订阅

```python showLineNumbers
from agentscope import MsgHub

# 创建MsgHub
msghub = MsgHub(agents=[agent_a, agent_b])

# 发布消息
await msghub.publish(Msg(name="user", content="开场"))

# 订阅结果
async for result in msghub.subscribe():
    print(result)
```

---

## 5. 模型配置模板

### OpenAI模型

```python showLineNumbers
model = agentscope.OpenAIChatModel(
    api_key="your-api-key",
    model="gpt-4",
    api_base="https://api.openai.com/v1"  # 可选：自定义端点
)
```

### Anthropic模型

```python showLineNumbers
model = agentscope.AnthropicChatModel(
    api_key="your-api-key",
    model="claude-3-sonnet-20240229"
)
```

### DashScope模型

```python showLineNumbers
model = agentscope.DashScopeChatModel(
    api_key="your-api-key",
    model="qwen-max"
)
```

---

## 6. 工具定义模板

### 基础工具

```python showLineNumbers
@Tool
def search_weather(city: str) -> str:
    """查询天气

    Args:
        city: 城市名称，如"北京"

    Returns:
        天气信息
    """
    # 实际实现中应该调用天气API
    return f"{city}今天晴，温度25度"
```

### 多参数工具

```python showLineNumbers
@Tool
def book_flight(from_city: str, to_city: str, date: str) -> str:
    """预订机票

    Args:
        from_city: 出发城市
        to_city: 目的城市
        date: 出发日期，格式YYYY-MM-DD

    Returns:
        预订结果
    """
    return f"已预订{from_city}到{to_city}的机票，日期{date}"
```

---

## 7. Runtime部署模板

```python showLineNumbers
from agentscope.runtime import AgentScopeRuntime

# 创建Runtime
runtime = AgentScopeRuntime(
    agents=[agent],
    host="0.0.0.0",
    port=5000
)

# 启动服务
runtime.start()

# 或异步启动
await runtime.start_async()
```

---

## 8. 异步调用模板

### 单Agent异步

```python showLineNumbers
import asyncio
import agentscope

async def main():
    agentscope.init()
    agent = agentscope.ReActAgent(name="test", model=..., sys_prompt="...")

    result = await agent("你好")
    print(result)

asyncio.run(main())
```

### 多Agent并发

```python showLineNumbers
import asyncio

async def main():
    # 并发调用多个Agent
    results = await asyncio.gather(
        agent_a("你好"),
        agent_b("你好"),
        agent_c("你好")
    )
    for r in results:
        print(r)

asyncio.run(main())
```

---

## 9. 错误处理模板

```python showLineNumbers
try:
    response = await agent("你好")
except agentscope.APIKeyError:
    print("API Key配置错误")
except agentscope.RateLimitError:
    print("请求过于频繁，请稍后重试")
except Exception as e:
    print(f"发生错误: {e}")
```

---

## 10. 常用配置模板

### 完整初始化配置

```python showLineNumbers
import agentscope

agentscope.init(
    project="MyAgentProject",
    description="这是一个智能助手项目",
    runtime_log_level="INFO",
    db_type="sqlite",  # 或 "redis"
    # db_type="redis",
    # redis_host="localhost",
    # redis_port=6379
)
```

---

**裁剪线**：沿线剪下，可放在桌上或贴在笔记本上方便随时参考
