# 第四章：核心概念

## 4.1 概念总览

AgentScope 有四个核心概念，理解它们就掌握了框架的精髓：

```
┌─────────────────────────────────────────────────────────────────┐
│                         核心概念                                  │
│                                                                 │
│  ┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐  │
│  │  Agent  │────▶│  Model  │────▶│  Tool   │────▶│ Memory  │  │
│  │ 智能体  │     │  模型   │     │  工具   │     │  记忆   │  │
│  └─────────┘     └─────────┘     └─────────┘     └─────────┘  │
│       │                                                   │     │
│       └─────────────────── MsgHub ───────────────────────┘     │
│                          (消息中心)                              │
└─────────────────────────────────────────────────────────────────┘
```

## 4.2 Agent (智能体)

### 什么是 Agent

**Agent = LLM + 推理引擎 + 工具 + 记忆**

```python
# Agent 的简化结构 (伪代码)
class Agent:
    def __init__(self, name, model, tools, memory):
        self.name = name
        self.model = model        # LLM 大脑
        self.tools = tools       # 工具箱
        self.memory = memory     # 记忆系统
        self.reasoning_engine = ReActEngine()  # 推理引擎
```

### Agent 的生命周期

```
┌─────────────────────────────────────────────────────────────┐
│ Agent 生命周期                                                │
│                                                              │
│  1. receive(user_input)                                      │
│         ↓                                                    │
│  2. think() ───▶ 思考用户意图，决定是否使用工具                  │
│         ↓                                                    │
│  3. act() ─────▶ 执行工具或直接回复                            │
│         ↓                                                    │
│  4. observe() ──▶ 获取工具执行结果                             │
│         ↓                                                    │
│  5. respond() ──▶ 生成最终回复                                 │
│                                                              │
│  类比 Java Servlet 生命周期: init() → service() → destroy()   │
└─────────────────────────────────────────────────────────────┘
```

### ReActAgent 详解

ReAct = **Re**asoning + **Act**ing

```python
from agentscope import agent
from agentscope.model import OpenAIChatModel

my_agent = agent.ReActAgent(
    name="助手",           # Agent 名称 (类似 Bean name)
    model=OpenAIChatModel(model_name="gpt-4o"),
    tools=[...],           # 可用工具列表
    memory=...,            # 记忆模块
    sys_prompt="你是一个有帮助的助手"  # 系统提示词
)
```

### Java 对比

| AgentScope | Java |
|------------|------|
| `agent.ReActAgent` | `@Service` class |
| `name` | `@Service("name")` |
| `model` | injected `Repository` |
| `tools` | injected `List<Bean>` |
| `memory` | injected `Cache` |
| `sys_prompt` | 类比 `application.yml` 中的 `spring.application.name` |

## 4.3 Model (模型)

### 模型架构

```
┌─────────────────────────────────────────┐
│         Model (模型抽象层)                │
├─────────────────────────────────────────┤
│                                         │
│  ┌─────────────┐  ┌─────────────┐       │
│  │ OpenAI     │  │ Anthropic  │       │
│  │ ChatGPT    │  │ Claude     │       │
│  └─────────────┘  └─────────────┘       │
│                                         │
│  ┌─────────────┐  ┌─────────────┐       │
│  │ DashScope  │  │ Gemini     │       │
│  │ 阿里通义    │  │ Google     │       │
│  └─────────────┘  └─────────────┘       │
│                                         │
│  ┌─────────────┐  ┌─────────────┐       │
│  │ Ollama     │  │ DeepSeek   │       │
│  │ 本地模型   │  │ 深度求索   │       │
│  └─────────────┘  └─────────────┘       │
│                                         │
└─────────────────────────────────────────┘
```

### 支持的模型类型

| 模型 | 调用方式 | 说明 |
|------|----------|------|
| `OpenAIChatModel` | API | GPT-4o, GPT-4-turbo, GPT-3.5 |
| `AnthropicChatModel` | API | Claude 3.5 Sonnet, Opus |
| `DashScopeChatModel` | API | 通义千问 (阿里云) |
| `GeminiChatModel` | API | Gemini 1.5, Gemini Pro |
| `OllamaChatModel` | 本地 | Llama3, Qwen2, Mistral |
| `DeepSeekChatModel` | API | DeepSeek Coder, Chat |

### 模型配置

```python
# 方式一：直接配置
model = OpenAIChatModel(
    model_name="gpt-4o",
    api_key="sk-xxxxx",
    temperature=0.7,        # 创造性 0-1
    max_tokens=4096         # 最大回复长度
)

# 方式二：模型配置字典 (推荐，便于管理)
model_config = {
    "model_name": "gpt-4o",
    "api_key": "sk-xxxxx",
    "temperature": 0.7
}
model = OpenAIChatModel(**model_config)
```

### Java 对比

```java
// Java: 依赖注入配置
@Service
public class MyService {
    private final Model model;

    @Autowired
    public MyService(@Value("${openai.api.key}") String apiKey) {
        this.model = new OpenAIModel(apiKey);
    }
}
```

```python
# Python: 直接传参
my_agent = ReActAgent(model=OpenAIChatModel(api_key="sk-xxxxx"))
```

## 4.4 Tool (工具)

### 工具类型

```
┌─────────────────────────────────────────────────────────┐
│                    工具类型                              │
│                                                         │
│  ┌───────────────┐  ┌───────────────┐  ┌─────────────┐  │
│  │ 代码执行      │  │ Shell 命令    │  │ API 调用   │  │
│  │ python_exec  │  │ bash/shell   │  │ http_call  │  │
│  └───────────────┘  └───────────────┘  └─────────────┘  │
│                                                         │
│  ┌───────────────┐  ┌───────────────┐  ┌─────────────┐  │
│  │ 文件操作      │  │ 数据库查询    │  │ MCP 协议   │  │
│  │ read/write   │  │ SQL query    │  │ mcp_tool   │  │
│  └───────────────┘  └───────────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 内置工具

```python
from agentscope.tool import execute_python_code
from agentscope.tool import execute_shell_command
from agentscope.tool import text_file

# 使用内置工具
my_agent = ReActAgent(
    tools=[
        execute_python_code,  # 执行 Python 代码
        execute_shell_command,   # 执行 Shell 命令
    ]
)
```

### 自定义工具

```python
from agentscope.tool import function

@function
def search_database(query: str, table: str = "users") -> str:
    """在数据库中搜索记录

    Args:
        query: 搜索关键词
        table: 表名，默认 'users'

    Returns:
        搜索结果 JSON 字符串
    """
    # 实际项目中连接真实数据库
    results = [{"id": 1, "name": "张三"}, {"id": 2, "name": "李四"}]
    return json.dumps(results, ensure_ascii=False)
```

### 工具装饰器

`@function` 装饰器会自动：
1. 解析函数签名，提取参数类型和描述
2. 生成 LLM 可见的工具描述 (JSON Schema)
3. 验证工具调用的参数

### Java 对比

```java
// Java: 手动定义工具接口
public interface Tool {
    String execute(Map<String, Object> params);
    String getDescription();
    Map<String, ParamSchema> getParameters();
}

@Component("searchDb")
public class SearchDatabaseTool implements Tool {
    @Override
    public String execute(Map<String, Object> params) {
        String query = (String) params.get("query");
        // 执行搜索...
    }
}
```

```python
# Python: 用装饰器自动完成
@function
def search_database(query: str, table: str = "users") -> str:
    # 装饰器自动提取参数和描述
    ...
```

## 4.5 Memory (记忆)

### 记忆类型

```
┌─────────────────────────────────────────────────────────┐
│                      记忆系统                             │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │              Working Memory (短期记忆)            │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐           │   │
│  │  │InMemory │ │  Redis  │ │SQLAlchemy│           │   │
│  │  └─────────┘ └─────────┘ └─────────┘           │   │
│  └─────────────────────────────────────────────────┘   │
│                         ↕                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │             Long-Term Memory (长期记忆)           │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐           │   │
│  │  │  Mem0   │ │  ReMe   │ │向量存储 │           │   │
│  │  └─────────┘ └─────────┘ └─────────┘           │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 使用记忆

```python
from agentscope.memory import InMemoryMemory

# 创建带记忆的 Agent
my_agent = agent.ReActAgent(
    name="助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    memory=InMemoryMemory()  # 默认实现，简单但有效
)

# 对话会被自动记录
my_agent("我叫张三")
my_agent("我叫什么名字？")  # 能记住"张三"
```

### Redis 记忆 (生产环境推荐)

```python
from agentscope.memory import RedisMemory

memory = RedisMemory(
    host="localhost",
    port=6379,
    key_prefix="my_agent:"
)

agent = agent.ReActAgent(
    name="助手",
    memory=memory
)
```

### Java 对比

| AgentScope | Java |
|------------|------|
| `InMemoryMemory` | `ConcurrentHashMap<String, Object>` |
| `RedisMemory` | Spring Cache + Redis |
| `Mem0` | External AI Memory Service |
| `向量存储` | Elasticsearch / Milvus |

## 4.6 MsgHub (消息中心)

MsgHub 用于多个 Agent 之间的消息传递和协调。

```python
from agentscope import pipeline

# 创建多个 Agent
agent_a = agent.ReActAgent(name="A", model=..., tools=...)
agent_b = agent.ReActAgent(name="B", model=..., tools=...)
agent_c = agent.ReActAgent(name="C", model=..., tools=...)

# 方式一：广播消息
with pipeline.MsgHub(agents=[agent_a, agent_b, agent_c], mode="broadcast"):
    agent_a("大家好！")  # B 和 C 都能收到
    agent_b("收到！")
    agent_c("我也收到！")

# 方式二：顺序执行
with pipeline.MsgHub(agents=[agent_a, agent_b], mode="sequential"):
    result_a = agent_a("分析一下销售数据")
    result_b = agent_b(f"基于这个分析写报告: {result_a}")
```

### Java 对比

```java
// Java: JMS / Kafka 消息队列
@Service
public class OrderService {
    private final JmsTemplate jmsTemplate;

    public void processOrder(Order order) {
        // 发送消息到队列
        jmsTemplate.convertAndSend("order.queue", order);
    }
}

// 或者使用 Spring Events
@Component
public class OrderEventListener {
    @EventListener
    public void handleOrderCreated(OrderCreatedEvent event) {
        // 处理事件
    }
}
```

## 4.7 下一步

- [第五章：架构设计](05_architecture.md) - 深入理解模块设计
- [第六章：开发指南](06_development_guide.md) - 掌握调试和测试
