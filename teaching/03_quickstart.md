# 第三章：快速入门

## 学习目标

> 学完本节，你将能够：
> - [L2 理解] 解释 AgentScope 中 Agent、Model、Formatter、Toolkit 四个核心概念的关系
> - [L3 应用] 使用正确的构造参数创建一个可运行的 ReActAgent
> - [L3 应用] 使用 Toolkit 注册自定义工具函数
> - [L4 分析] 比较 SequentialPipeline 和 FanoutPipeline 的适用场景

**预计时间**：20 分钟
**先修要求**：已完成 [第二章：环境搭建](02_installation.md)

## 3.1 概念预览

在开始之前，了解 AgentScope 的核心概念：

```
┌─────────────────────────────────────────────────────────┐
│  Agent (智能体)                                          │
│  ┌─────────────────────────────────────────────────┐  │
│  │ 一个带 "大脑" (LLM) 的自主执行单元                  │  │
│  │ - 能够思考 (Reasoning)                           │  │
│  │ - 能够行动 (Acting)                              │  │
│  │ - 能够使用工具 (Tool Use)                        │  │
│  │ - 有记忆 (Memory)                                │  │
│  └─────────────────────────────────────────────────┘  │
│                                                          │
│  类比 Java: 一个 @Service + 内置 AI 能力                  │
└─────────────────────────────────────────────────────────┘
```

### Agent 类型 (v1.0.19)

| Agent 类型 | 说明 | 使用场景 |
|------------|------|----------|
| `ReActAgent` | 核心推理 Agent，支持 ReAct 范式 | 通用任务、工具调用、纯对话 |
| `UserAgent` | 用户代理，在终端或 Studio 中接收用户输入 | 用户操作模拟、人机协作 |
| `A2AAgent` | 基于 Agent-to-Agent 协议的通信 Agent | 多 Agent 跨服务协作 |
| `RealtimeAgent` | 实时语音/视频 Agent | 语音交互、实时对话 |

> **注意**：`ReActAgent` 是最通用的 Agent 类型。不配置 toolkit 时即为纯对话模式，配置 toolkit 后自动支持工具调用。

## 3.2 5 分钟快速构建

> **核心要点**：AgentScope 的 Agent 调用是**异步**的。所有示例需要在 `async` 函数中运行。

### Step 1: 创建项目文件

```python
# quickstart.py

import asyncio
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit, execute_python_code

async def main():
    # Step 1: 初始化框架
    agentscope.init(
        project="my-first-agent"
        # api_key 通过环境变量 OPENAI_API_KEY 设置
    )

    # Step 2: 准备工具
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_python_code)

    # Step 3: 创建 Agent
    my_agent = ReActAgent(
        name="助手",
        sys_prompt="你是一个有帮助的编程助手。",
        model=OpenAIChatModel(
            model_name="gpt-4o"
            # api_key 通过环境变量 OPENAI_API_KEY 设置
        ),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
    )

    # Step 4: 运行 Agent
    response = await my_agent("请用 Python 写一个快速排序算法")
    print(response)

if __name__ == "__main__":
    asyncio.run(main())
```

**关键点解析**：

| 概念 | 代码 | Java 对比 |
|------|------|-----------|
| Agent | `ReActAgent(...)` | `@Service` + 业务逻辑 |
| Model | `OpenAIChatModel(...)` | 注入外部 API 客户端 |
| Formatter | `OpenAIChatFormatter()` | 消息序列化器，匹配 Model 提供商 |
| Toolkit | `Toolkit()` + `register_tool_function()` | `@Bean` 注册工具方法 |
| sys_prompt | `"你是一个有帮助的编程助手。"` | `@PostConstruct` 初始化行为指令 |

> **为什么需要 Formatter？** 不同模型提供商（OpenAI、Anthropic、DashScope 等）的消息格式不同。Formatter 负责将统一的 `Msg` 对象转换为特定提供商的格式。`OpenAIChatModel` 对应 `OpenAIChatFormatter`，其他模型同理。

### Step 2: 运行

```bash
python quickstart.py
```

**预期输出**：

```
[2026-05-01 10:00:00] Agent 助手 created with id: abc123
[2026-05-01 10:00:01] User: 请用 Python 写一个快速排序算法
[2026-05-01 10:00:05] Assistant: 好的，这是一个快速排序算法的 Python 实现...
```

## 3.3 代码解析

```
Java 对比分析:

┌─────────────────────────────────────────────────────────┐
│ Java 写法 (Spring Boot)                                  │
├─────────────────────────────────────────────────────────┤
│ @Service                                                 │
│ public class OrderService {                              │
│     private final OrderRepository repository;            │
│                                                          │
│     public OrderService(OrderRepository repository) {   │
│         this.repository = repository;                    │
│     }                                                    │
│                                                          │
│     public Order createOrder(OrderReq req) {            │
│         return repository.save(req);                     │
│     }                                                    │
│ }                                                        │
└─────────────────────────────────────────────────────────┘

                          ↓  对比  ↓

┌─────────────────────────────────────────────────────────┐
│ Python/AgentScope 写法                                   │
├─────────────────────────────────────────────────────────┤
│ agent = ReActAgent(                                      │
│     name="助手",              # ← 类名（Spring Bean名）    │
│     sys_prompt="...",        # ← 初始化行为指令            │
│     model=OpenAIChatModel(), # ← 注入依赖（@Autowired）    │
│     formatter=...,           # ← 消息序列化器              │
│     toolkit=toolkit,         # ← 工具集（@Bean方法集合）    │
│ )                                                        │
│                                                          │
│ response = await agent(query) # ← 调用业务方法             │
└─────────────────────────────────────────────────────────┘
```

## 3.4 使用 Ollama 本地模型

如果你不想付费使用 OpenAI，可以使用 Ollama 运行本地模型：

```python
import asyncio
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OllamaChatModel
from agentscope.formatter import OllamaChatFormatter

async def main():
    agentscope.init(project="local-agent")

    # 使用 Ollama (类似使用 H2 数据库代替 MySQL)
    local_agent = ReActAgent(
        name="本地助手",
        sys_prompt="你是一个有帮助的助手。",
        model=OllamaChatModel(
            model_name="llama3.2",  # 或 qwen2.5, mistral 等
            host="http://localhost:11434"  # 注意：参数名是 host，不是 base_url
        ),
        formatter=OllamaChatFormatter(),
    )

    response = await local_agent("你好，请介绍一下你自己")
    print(response)

if __name__ == "__main__":
    asyncio.run(main())
```

启动 Ollama：

```bash
# 安装 Ollama
brew install ollama  # macOS
# Windows: 下载 https://ollama.com

# 下载模型
ollama pull llama3.2

# 启动服务 (默认 11434 端口)
ollama serve
```

## 3.5 添加自定义工具

AgentScope 的真正威力在于让 Agent 调用工具。

```python
import asyncio
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

async def main():
    agentscope.init(project="tool-agent")

    # 定义工具函数（普通 Python 函数即可，无需装饰器）
    def get_weather(city: str) -> ToolResponse:
        """获取城市天气

        Args:
            city: 城市名称
        """
        weather_data = {
            "北京": "晴，25°C",
            "上海": "阴，22°C",
            "广州": "雨，28°C"
        }
        result = weather_data.get(city, "未知城市")
        return ToolResponse(content=[TextBlock(type="text", text=result)])

    def calculate(a: float, b: float) -> ToolResponse:
        """计算两个数的乘积

        Args:
            a: 第一个数
            b: 第二个数
        """
        result = a * b
        return ToolResponse(content=[TextBlock(type="text", text=str(result))])

    # 注册工具到 Toolkit
    toolkit = Toolkit()
    toolkit.register_tool_function(get_weather)
    toolkit.register_tool_function(calculate)

    # 创建带工具的 Agent
    weather_agent = ReActAgent(
        name="天气助手",
        sys_prompt="你是一个天气查询助手，可以查询天气和进行数学计算。",
        model=OpenAIChatModel(model_name="gpt-4o"),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
    )

    # Agent 会自动决定何时调用工具
    response = await weather_agent("北京今天天气怎么样？顺便帮我算一下 123 乘以 456")
    print(response)

if __name__ == "__main__":
    asyncio.run(main())
```

**关键变化**：

| 旧写法 (错误) | 新写法 (正确) | 原因 |
|---------------|---------------|------|
| `@function` 装饰器 | 无需装饰器 | AgentScope 使用 Toolkit 注册，不用装饰器 |
| `tools=[func1, func2]` | `Toolkit()` + `register_tool_function()` | ReActAgent 接受 `toolkit` 参数，不是 `tools` 列表 |
| 返回 `str` | 返回 `ToolResponse(content=[TextBlock(...)])` | 工具函数应返回标准 ToolResponse |

## 3.6 工具调用流程解析

```
┌──────────────────────────────────────────────────────────────┐
│ Agent 工具调用流程                                             │
│                                                               │
│  User: "北京今天天气怎么样？"                                    │
│                        ↓                                      │
│  ┌─────────────┐                                             │
│  │   Agent     │  1. 理解用户意图                               │
│  │  (思考中...) │  ──────────────────────────────────────     │
│  └──────┬──────┘                                             │
│         ↓                                                     │
│  ┌─────────────────────────────────────────┐                 │
│  │ LLM 决定调用 get_weather(city="北京")      │ ← Tool Use    │
│  └──────┬──────────────────────────────────┘                 │
│         ↓                                                     │
│  ┌─────────────┐                                             │
│  │  工具执行   │  get_weather("北京") → "晴，25°C"             │
│  └──────┬──────┘                                             │
│         ↓                                                     │
│  ┌─────────────────────────────────────────┐                 │
│  │ LLM 组织回复: "北京今天天气晴，气温25°C"    │ ← Response    │
│  └─────────────────────────────────────────┘                 │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

## 3.7 对话式 Agent

如果只需要简单的对话，不需要工具调用：

```python
import asyncio
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter

async def main():
    agentscope.init(project="chatbot")

    # 简单的对话 Agent（不配置 toolkit，即为纯对话模式）
    chat_agent = ReActAgent(
        name="小助手",
        sys_prompt="你是一个友好的聊天助手。",
        model=OpenAIChatModel(model_name="gpt-4o-mini"),
        formatter=OpenAIChatFormatter(),
    )

    # 循环对话
    while True:
        user_input = input("你: ")
        if user_input.lower() in ["exit", "quit", "退出"]:
            break
        response = await chat_agent(user_input)
        print(f"助手: {response}")

if __name__ == "__main__":
    asyncio.run(main())
```

## 3.8 多 Agent 协作

AgentScope 支持多种多 Agent 协作模式：

```python
import asyncio
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.pipeline import SequentialPipeline, FanoutPipeline

async def main():
    agentscope.init(project="multi-agent")

    # 创建多个 Agent
    researcher = ReActAgent(
        name="研究员",
        sys_prompt="你是一个研究助手，负责收集和分析信息。",
        model=OpenAIChatModel(model_name="gpt-4o"),
        formatter=OpenAIChatFormatter(),
    )

    writer = ReActAgent(
        name="作家",
        sys_prompt="你是一个技术写作助手，负责将研究结果整理成文章。",
        model=OpenAIChatModel(model_name="gpt-4o-mini"),
        formatter=OpenAIChatFormatter(),
    )

    # 模式一：SequentialPipeline - 顺序执行
    # 第一个 Agent 的输出会自动传递给第二个 Agent
    seq = SequentialPipeline(agents=[researcher, writer])
    result = await seq("研究 AI Agent 的最新发展趋势")

    # 模式二：FanoutPipeline - 并行执行
    # 同一个输入同时发送给所有 Agent
    fanout = FanoutPipeline(agents=[researcher, writer])
    results = await fanout("研究 AI Agent 的最新发展趋势")

if __name__ == "__main__":
    asyncio.run(main())
```

**Pipeline 对比**：

| Pipeline 类型 | 执行方式 | 输入 | 输出 | 适用场景 |
|---------------|----------|------|------|----------|
| `SequentialPipeline` | 顺序链式 | 第一个 Agent 的初始消息 | 最后一个 Agent 的输出 | 工作流：研究→写作→审核 |
| `FanoutPipeline` | 并行广播 | 同一消息发给所有 Agent | 所有 Agent 输出的列表 | 多视角分析、投票 |

## 3.9 常见错误排查

| 错误信息 | 原因 | 解决方法 |
|----------|------|----------|
| `TypeError: missing required argument 'sys_prompt'` | ReActAgent 构造缺少必填参数 | 添加 `sys_prompt` 和 `formatter` 参数 |
| `TypeError: missing required argument 'formatter'` | 同上 | 添加对应 Model 提供商的 Formatter |
| `ImportError: cannot import name 'function'` | `@function` 装饰器不存在 | 使用 `Toolkit.register_tool_function()` |
| `RuntimeWarning: coroutine was never awaited` | 忘记 `await` Agent 调用 | 使用 `await agent(...)` 或 `asyncio.run()` |
| `APIKeyError` | API Key 未设置或无效 | 检查 `OPENAI_API_KEY` 环境变量 |
| `ConnectionError` | 网络问题 / 代理 | 检查 VPN / proxy 设置 |

## 知识检查

1. **ReActAgent 的四个必填参数是什么？**
   <details><summary>查看答案</summary>
   <code>name</code>, <code>sys_prompt</code>, <code>model</code>, <code>formatter</code>
   </details>

2. **为什么需要 Formatter？可以用任意 Formatter 配合任意 Model 吗？**
   <details><summary>查看答案</summary>
   不能。Formatter 负责将统一的 Msg 对象转换为特定模型提供商的格式。必须使用与 Model 提供商匹配的 Formatter（如 <code>OpenAIChatModel</code> 对应 <code>OpenAIChatFormatter</code>）。
   </details>

3. **如何将一个普通 Python 函数注册为 Agent 可调用的工具？**
   <details><summary>查看答案</summary>
   创建 <code>Toolkit</code> 实例，调用 <code>toolkit.register_tool_function(func)</code> 注册函数，然后将 toolkit 传给 <code>ReActAgent(toolkit=toolkit)</code>。
   </details>

## 总结

- **ReActAgent** 是 AgentScope 的核心 Agent 类型，需要四个必填参数：`name`, `sys_prompt`, `model`, `formatter`
- **Toolkit** 用于注册工具函数，通过 `register_tool_function()` 方法添加
- **Formatter** 必须与 Model 提供商匹配（OpenAI → OpenAIChatFormatter）
- **所有 Agent 调用都是异步的**，需要 `await` 或 `asyncio.run()` 包装
- **SequentialPipeline** 用于顺序工作流，**FanoutPipeline** 用于并行广播

## 下一章

→ [第四章：核心概念](04_core_concepts.md) - 深入理解 Agent/Model/Tool/Memory
