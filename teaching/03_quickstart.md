# 第三章：快速入门

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
| `ReActAgent` | 核心推理 Agent，支持 ReAct 范式 | 通用任务、工具调用 |
| `DialogAgent` | 简单对话 Agent | 纯对话场景 |
| `DeepResearchAgent` | 深度研究 Agent | 网络搜索、多轮研究 |
| `RealtimeAgent` | 实时语音 Agent | 语音交互 |
| `UserAgent` | 用户代理 | 用户操作模拟 |
| `A2AAgent` | Agent 间通信 | 多 Agent 协作 |

## 3.2 5 分钟快速构建

### Step 1: 创建项目文件

```python
# quickstart.py

import agentscope
from agentscope import agent
from agentscope.model import OpenAIChatModel
from agentscope.tool import execute_python_code

# Step 1: 初始化框架
agentscope.init(
    project="my-first-agent"
    # api_key 通过环境变量 OPENAI_API_KEY 设置
)

# Step 2: 创建 Agent
my_agent = agent.ReActAgent(
    name="助手",
    model=OpenAIChatModel(
        model_name="gpt-4o"
        # api_key 通过环境变量 OPENAI_API_KEY 设置
    ),
    # 内置工具：Python 代码执行
    tools=[execute_python_code]
)

# Step 3: 运行 Agent
response = my_agent("请用 Python 写一个快速排序算法")
print(response)
```

### Step 4: 运行

```bash
python quickstart.py
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
│ class MyAgent:  # ← 不需要装饰器                          │
│     def __init__(self, model):  # ← 构造函数注入          │
│         self.model = model                               │
│                                                          │
│     def run(self, query):  # ← 直接方法调用               │
│         return self.model(query)                         │
└─────────────────────────────────────────────────────────┘
```

## 3.4 使用 Ollama 本地模型

如果你不想付费使用 OpenAI，可以使用 Ollama 运行本地模型：

```python
import agentscope
from agentscope.model import OllamaChatModel

agentscope.init(project="local-agent")

# 使用 Ollama (类似使用 H2 数据库代替 MySQL)
local_agent = agent.ReActAgent(
    name="本地助手",
    model=OllamaChatModel(
        model_name="llama3.2",  # 或 qwen2.5, mistral 等
        base_url="http://localhost:11434"
    )
)

response = local_agent("你好，请介绍一下你自己")
print(response)
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
import agentscope
from agentscope import agent
from agentscope.model import OpenAIChatModel
from agentscope.tool import function

# 定义一个工具 (类似 Java 的 @Bean / Util 方法)
@function
def get_weather(city: str) -> str:
    """获取城市天气"""
    # 实际项目中这里会调用天气 API
    weather_data = {
        "北京": "晴，25°C",
        "上海": "阴，22°C",
        "广州": "雨，28°C"
    }
    return weather_data.get(city, "未知城市")

@function
def calculate(expression: str) -> float:
    """计算数学表达式

    ⚠️ 安全警告: 此示例使用 eval() 仅供教学演示。
    生产环境应使用 ast.literal_eval() 或专用数学库如 numexpr，
    并对输入进行严格的格式验证，防止代码注入攻击。
    """
    return eval(expression)  # 教学演示，生产环境禁用

# 创建带工具的 Agent
weather_agent = agent.ReActAgent(
    name="天气助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    tools=[get_weather, calculate]  # 注入工具
)

# Agent 会自动决定何时调用工具
response = weather_agent("北京今天天气怎么样？顺便帮我算一下 123 * 456")
print(response)
```

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

如果只需要简单的对话，不需要 ReAct 推理：

```python
from agentscope import agent
from agentscope.model import OpenAIChatModel
import agentscope

agentscope.init(project="chatbot")

# 简单的对话 Agent (不需要工具)
chat_agent = agent.ReActAgent(
    name="小助手",
    model=OpenAIChatModel(model_name="gpt-4o-mini"),
    tools=[]  # 不使用工具，纯对话
)

# 循环对话
while True:
    user_input = input("你: ")
    if user_input.lower() in ["exit", "quit", "退出"]:
        break
    response = chat_agent(user_input)
    print(f"助手: {response}")
```

## 3.8 多 Agent 协作

AgentScope 支持多种多 Agent 协作模式：

```python
import agentscope
from agentscope import agent
from agentscope.pipeline import SequentialPipeline, FanoutPipeline
from agentscope.model import OpenAIChatModel

agentscope.init(project="multi-agent")

# 创建多个 Agent
researcher = agent.ReActAgent(
    name="研究员",
    model=OpenAIChatModel(model_name="gpt-4o"),
    tools=[...]  # 可以联网搜索的工具
)

writer = agent.ReActAgent(
    name="作家",
    model=OpenAIChatModel(model_name="gpt-4o-mini")
)

# 模式一：FanoutPipeline - 并行执行
with FanoutPipeline(agents=[researcher, writer]) as fanout:
    results = fanout("研究 AI Agent 的最新发展趋势")

# 模式二：SequentialPipeline - 顺序执行
with SequentialPipeline(agents=[researcher, writer]) as seq:
    research_result = researcher("研究 AI 发展趋势")
    article = writer(f"基于研究写文章: {research_result}")
```

### 3.8.1 DeepResearchAgent 深度研究

DeepResearchAgent 是 v1.0.19 新增的强大研究 Agent，支持多轮网络搜索和信息综合：

```python
from agentscope.agent import DeepResearchAgent
from agentscope.model import OpenAIChatModel

research_agent = DeepResearchAgent(
    name="深度研究助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    tools=[tavily_search, tavily_extract],  # 需要 Tavily API
    max_depth=3,       # 研究深度
    max_tokens=100000   # 研究报告最大长度
)

report = research_agent("研究 AI 在医疗领域的应用现状和未来趋势")
print(report)
```

## 3.9 常见错误排查

| 错误信息 | 原因 | 解决方法 |
|----------|------|----------|
| `APIKeyError` | API Key 未设置或无效 | 检查 `OPENAI_API_KEY` 环境变量 |
| `ConnectionError` | 网络问题 / 代理 | 检查 VPN / proxy 设置 |
| `RateLimitError` | 请求过于频繁 | 添加 `time.sleep()` 或升级账户 |
| `ModelNotFoundError` | 模型名称错误 | 检查模型名称，如 `gpt-4o` 而非 `gpt4` |
| `ImportError` | 缺少依赖 | `pip install agentscope[models]` |

## 3.10 下一步

- [第四章：核心概念](04_core_concepts.md) - 深入理解 Agent/Model/Tool/Memory
- [第五章：架构设计](05_architecture.md) - 理解框架内部设计
