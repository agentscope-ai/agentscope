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

```python showLineNumbers
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
            model_name="gpt-4o",
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

```python showLineNumbers
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

**预期输出**：

```
[2026-05-05 10:00:00] Agent 本地助手 created with id: local456
[2026-05-05 10:00:01] User: 你好，请介绍一下你自己
[2026-05-05 10:00:05] Assistant: 你好！我是 LLaMA3.2，一个由 Meta 开发的开源大语言模型...
```

## 3.5 添加自定义工具

AgentScope 的真正威力在于让 Agent 调用工具。

```python showLineNumbers
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

**预期输出**：

```
[2026-05-05 10:00:00] Agent 天气助手 created with id: tool789
[2026-05-05 10:00:01] User: 北京今天天气怎么样？顺便帮我算一下 123 乘以 456
[2026-05-05 10:00:02] [TOOL_CALL] get_weather(city="北京") -> "晴，25°C"
[2026-05-05 10:00:03] [TOOL_CALL] calculate(a=123, b=456) -> "56088"
[2026-05-05 10:00:06] Assistant: 北京今天天气晴，气温25°C。另外，123乘以456等于56088。
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

```python showLineNumbers
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

**对话示例**：

```
你: 你好
[2026-05-05 10:00:02] Assistant: 你好！有什么我可以帮助你的吗？
你: 北京天气如何
[2026-05-05 10:00:05] Assistant: 抱歉，我没有天气查询功能。
你: 退出
[2026-05-05 10:00:08] 会话结束
```

## 3.8 多 Agent 协作

AgentScope 支持多种多 Agent 协作模式：

```python showLineNumbers
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

**预期输出**：

```
# SequentialPipeline 示例（顺序执行）
[2026-05-05 10:00:00] Agent 研究员 created with id: res001
[2026-05-05 10:00:00] Agent 作家 created with id: writer002
[2026-05-05 10:00:01] Pipeline SequentialPipeline initialized with [研究员, 作家]
[2026-05-05 10:00:02] User: 研究 AI Agent 的最新发展趋势
[2026-05-05 10:00:03] [研究员] 正在研究...
[2026-05-05 10:00:05] [研究员] -> [作家]: 研究完成，开始写作
[2026-05-05 10:00:06] [作家] 正在整理文章...
[2026-05-05 10:00:10] Assistant (作家): 以下是关于 AI Agent 最新发展趋势的研究报告...

# FanoutPipeline 示例（并行执行）
[2026-05-05 10:00:00] Agent 研究员 created with id: res001
[2026-05-05 10:00:00] Agent 作家 created with id: writer002
[2026-05-05 10:00:01] Pipeline FanoutPipeline initialized with [研究员, 作家]
[2026-05-05 10:00:02] User: 研究 AI Agent 的最新发展趋势
[2026-05-05 10:00:03] [研究员] 正在并行研究...
[2026-05-05 10:00:03] [作家] 正在并行分析...
[2026-05-05 10:00:06] Results: [<Msg from=研究员>, <Msg from=作家>]
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

## 练习题

### 练习 3.1: ReActAgent 构造 [基础]

**题目**：
以下代码尝试创建一个 ReActAgent，请检查是否有错误：

```python showLineNumbers
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel

agent = ReActAgent(
    name="助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个有帮助的助手。",
    # 缺少 formatter
    toolkit=Toolkit(),
)
```

**验证方式**：
运行代码，检查是否报错，并说明原因。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**错误**：缺少 `formatter` 参数

**原因**：ReActAgent 的四个必填参数是 `name`、`sys_prompt`、`model`、`formatter`。`formatter` 负责将消息转换为特定模型的格式，不能省略。

**修正后的代码**：
```python showLineNumbers
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter  # 需要添加导入

agent = ReActAgent(
    name="助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个有帮助的助手。",
    formatter=OpenAIChatFormatter(),  # 必须提供
    toolkit=Toolkit(),
)
```

**Formatter 与 Model 的匹配关系**：
| Model | Formatter |
|-------|-----------|
| `OpenAIChatModel` | `OpenAIChatFormatter` |
| `AnthropicChatModel` | `AnthropicChatFormatter` |
| `DashScopeChatModel` | `DashScopeChatFormatter` |
| `OllamaChatModel` | `OllamaChatFormatter` |
</details>

---

### 练习 3.2: 工具注册 [中级]

**题目**：
小张编写了一个计算器工具函数，但 Agent 无法正确调用它。请检查以下代码的问题：

```python showLineNumbers
from agentscope.tool import Toolkit

def calculate(a: float, b: float) -> float:
    """计算两个数的乘积"""
    return a * b

toolkit = Toolkit()
# 小张不确定这里应该怎么写
toolkit.register_tool_function(calculate)  # 这样对吗？
```

**验证方式**：
对比文档中的工具注册示例进行验证。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**问题 1**：缺少 `group_name` 参数

虽然 `register_tool_function()` 的 `group_name` 参数有默认值（`"basic"`），但最好显式指定。

**问题 2**：函数返回类型错误

工具函数应返回 `ToolResponse`，而非直接返回值。

**修正后的代码**：
```python showLineNumbers
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

def calculate(a: float, b: float) -> ToolResponse:
    """计算两个数的乘积

    Args:
        a: 第一个数
        b: 第二个数
    """
    result = a * b
    # 必须返回 ToolResponse
    return ToolResponse(content=[TextBlock(type="text", text=str(result))])

toolkit = Toolkit()
toolkit.register_tool_function(calculate, group_name="basic")
```

**关键点**：
1. 工具函数应返回 `ToolResponse(content=[TextBlock(...)])`
2. 需要 `group_name` 来组织工具组
3. docstring 中的 Args 描述会被 AgentScope 用于生成工具的 JSON Schema
</details>

---

### 练习 3.3: Pipeline 选择 [基础]

**题目**：
某公司需要构建一个投票系统，用户输入一个议题后，需要同时让 3 个专家 Agent 给出意见，然后汇总。请选择合适的 Pipeline 类型并说明理由。

**验证方式**：
检查答案是否正确识别场景需求和对应的 Pipeline。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**推荐选择**：FanoutPipeline（并行管道）

**理由**：
- 用户希望 3 个专家**同时**给出意见
- FanoutPipeline 将同一消息广播给所有 Agent，所有 Agent 并行处理
- 最后汇总所有 Agent 的输出

**代码示例**：
```python showLineNumbers
from agentscope.pipeline import FanoutPipeline

# 创建专家 Agent
expert1 = ReActAgent(name="专家1", ...)
expert2 = ReActAgent(name="专家2", ...)
expert3 = ReActAgent(name="专家3", ...)

# 构建广播管道
voting_pipeline = FanoutPipeline(agents=[expert1, expert2, expert3])

# 并行收集意见
results = await voting_pipeline("我们应该推广远程办公吗？")

# results 是一个列表，包含三个专家的意见
for result in results:
    print(f"{result.name}: {result.content}")
```

**Pipeline 对比**：
| Pipeline 类型 | 执行方式 | 适用场景 |
|---------------|----------|----------|
| `SequentialPipeline` | 顺序链式 | 研究→写作→审核 |
| `FanoutPipeline` | 并行广播 | 投票、意见收集、多视角分析 |
</details>

---

### 练习 3.4: 异步调用 [中级]

**题目**：
以下代码运行时没有任何输出，也没有任何错误，请分析原因：

```python showLineNumbers
import agentscope
from agentscope.agent import ReActAgent

agentscope.init(project="test")

agent = ReActAgent(
    name="助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个有帮助的助手。",
    formatter=OpenAIChatFormatter(),
)

# 调用 Agent
response = agent("你好，请介绍一下你自己")
print(response)
```

**验证方式**：
分析代码，找出异步调用的问题。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**问题**：缺少 `await` 和 `asyncio.run()`

**原因分析**：
1. `ReActAgent` 的调用是**异步**的，必须使用 `await`
2. 异步函数必须在 `async` 函数中运行
3. 最外层需要 `asyncio.run()` 包装

**修正后的代码**：
```python showLineNumbers
import asyncio
import agentscope
from agentscope.agent import ReActAgent

agentscope.init(project="test")

agent = ReActAgent(
    name="助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个有帮助的助手。",
    formatter=OpenAIChatFormatter(),
)

async def main():
    # 使用 await 调用
    response = await agent("你好，请介绍一下你自己")
    print(response)

# 使用 asyncio.run 包装
asyncio.run(main())
```

**常见错误**：
| 错误写法 | 正确写法 |
|----------|----------|
| `agent("msg")` | `await agent("msg")` |
| `asyncio.run(agent("msg"))` | `asyncio.run(main())` 其中 `main()` 内使用 `await` |
| `RuntimeWarning: coroutine was never awaited` | 检查是否缺少 `await` |
</details>

---

### 练习 3.5: Ollama 本地模型配置 [挑战]

**题目**：
某开发者想使用 Ollama 本地模型，但 Agent 返回了连接错误。请检查以下配置是否正确：

```python showLineNumbers
from agentscope.model import OllamaChatModel

model = OllamaChatModel(
    model_name="llama3.2",
    base_url="http://localhost:11434",  # 第4行
    stream=True,
)
```

如果有问题，请指出并修正。

**验证方式**：
对比文档中 Ollama 模型配置参数。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**错误**：参数名错误

**问题**：第 4 行使用了 `base_url`，但 Ollama 模型的实际参数名是 `host`

**修正后的代码**：
```python showLineNumbers
from agentscope.model import OllamaChatModel

model = OllamaChatModel(
    model_name="llama3.2",
    host="http://localhost:11434",  # 参数名是 host，不是 base_url
    stream=True,
)
```

**常见模型参数对比**：

| 模型 | URL 参数名 |
|------|-----------|
| `OllamaChatModel` | `host` |
| `OpenAIChatModel` | `base_url`（可选，用于代理） |
| `DashScopeChatModel` | `base_http_api_url`（可选） |

**其他 Ollama 配置参数**：
```python showLineNumbers
model = OllamaChatModel(
    model_name="llama3.2",
    host="http://localhost:11434",
    stream=True,
    options={
        "temperature": 0.7,
        "num_ctx": 4096,  # 上下文窗口大小
    },
    keep_alive="5m",  # 模型在内存中保持时间
)
```
</details>

## 总结

- **ReActAgent** 是 AgentScope 的核心 Agent 类型，需要四个必填参数：`name`, `sys_prompt`, `model`, `formatter`
- **Toolkit** 用于注册工具函数，通过 `register_tool_function()` 方法添加
- **Formatter** 必须与 Model 提供商匹配（OpenAI → OpenAIChatFormatter）
- **所有 Agent 调用都是异步的**，需要 `await` 或 `asyncio.run()` 包装
- **SequentialPipeline** 用于顺序工作流，**FanoutPipeline** 用于并行广播

## 下一章

→ [第四章：核心概念](04_core_concepts.md) - 深入理解 Agent/Model/Tool/Memory
