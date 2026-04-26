# 第五章：架构设计

## 5.1 整体架构

AgentScope 采用**分层模块化架构**，设计原则类似 Java 的 Spring Framework：

```
┌─────────────────────────────────────────────────────────────────┐
│                        AgentScope                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Application Layer                     │   │
│  │                   (应用层 - examples/)                    │   │
│  │   agent/  |  functionality/  |  workflows/  |  game/   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              ↓                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                     Service Layer                        │   │
│  │                    (服务层 - src/)                        │   │
│  │                                                          │   │
│  │   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  │   │
│  │   │  Agent  │  │  Model  │  │   Tool  │  │ Memory  │  │   │
│  │   └─────────┘  └─────────┘  └─────────┘  └─────────┘  │   │
│  │                                                          │   │
│  │   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  │   │
│  │   │Pipeline │  │  Form.  │  │   RAG   │  │Tracing  │  │   │
│  │   └─────────┘  └─────────┘  └─────────┘  └─────────┘  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              ↓                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   Infrastructure Layer                    │   │
│  │                      (基础设施层)                          │   │
│  │   HTTP Client  |  SocketIO  |  Database  |  Redis     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 5.2 目录结构详解

```
src/agentscope/
├── __init__.py              # 包入口，定义 init() 函数
│
├── _version.py              # 版本信息
├── _logging.py              # 日志配置 (类似 logback.xml)
├── _run_config.py           # 运行时配置
│
├── agent/                   # ════════════════════════════════
│   ├── _agent_base.py       # Agent 基类 (抽象类)
│   ├── _react_agent.py      # ReAct Agent 实现
│   ├── _user_agent.py       # 用户交互 Agent
│   ├── _realtime_agent.py   # 实时语音 Agent
│   ├── _a2a_agent.py        # A2A 协议 Agent
│   └── __init__.py          # 导出 ReActAgent, UserAgent 等
│
├── model/                   # ════════════════════════════════
│   ├── _model_base.py       # 模型基类 (抽象类)
│   ├── _openai_model.py     # OpenAI 实现
│   ├── _anthropic_model.py  # Anthropic Claude 实现
│   ├── _dashscope_model.py  # 阿里通义实现
│   ├── _gemini_model.py     # Google Gemini 实现
│   ├── _ollama_model.py     # Ollama 本地模型
│   └── __init__.py
│
├── tool/                    # ════════════════════════════════
│   ├── _tool_base.py        # 工具基类
│   ├── _coding.py           # 代码执行工具 (Python, Shell)
│   ├── _text_file.py        # 文件操作工具
│   ├── _multi_modality.py   # 多模态工具
│   ├── _mcp.py              # MCP 协议工具
│   └── __init__.py
│
├── memory/                  # ════════════════════════════════
│   ├── _memory_base.py      # 记忆基类
│   ├── _working_memory/     # 短期记忆
│   │   ├── _in_memory.py
│   │   ├── _redis.py
│   │   └── _sqlalchemy.py
│   ├── _long_term_memory/   # 长期记忆
│   │   ├── _mem0.py
│   │   └── _reme.py
│   └── __init__.py
│
├── pipeline/                # ════════════════════════════════
│   ├── _msghub.py           # 消息中心
│   ├── _functional.py       # 函数式管道
│   └── __init__.py
│
├── formatter/               # 消息格式化器 (适配不同 API)
│
├── rag/                     # RAG 相关
│   ├── _reader/             # 文档读取器
│   └── _store/             # 向量存储
│
├── session/                 # 会话管理
├── embedding/               # Embedding 模型
├── token/                   # Token 计数
├── evaluate/                # 评估基准
├── tracing/                 # 链路追踪
├── realtime/                # 实时通信
├── tts/                     # 语音合成
├── tuner/                   # 模型调优
├── mcp/                     # MCP 协议
├── a2a/                     # A2A 协议
└── _utils/                  # 工具函数
```

## 5.3 核心类设计

### Agent 继承体系

```
                    ABC (Abstract Base Class)
                         │
                         ▼
                   AgentBase
                   (抽象基类)
                    /    \
                   /      \
           UserAgent   ReActAgent
                         │
                         ├── RealtimeAgent
                         └── A2AAgent
```

```python
# _agent_base.py (简化)
class AgentBase(ABC):
    """Agent 抽象基类"""

    def __init__(
        self,
        name: str,
        model: ModelWrapperBase,
        sys_prompt: str | None = None,
    ):
        self.name = name
        self.model = model
        self.sys_prompt = sys_prompt

    @abstractmethod
    def reply(self, msg: str | Msg) -> str:
        """处理消息并返回回复"""
        pass
```

```python
# _react_agent.py
class ReActAgent(AgentBase):
    """ReAct 推理 Agent"""

    def __init__(
        self,
        name: str,
        model: ModelWrapperBase,
        tools: list[Tool] | None = None,
        memory: MemoryBase | None = None,
        max_retries: int = 3,
    ):
        super().__init__(name, model, ...)
        self.tools = tools or []
        self.memory = memory or InMemoryMemory()

    def reply(self, msg: str | Msg) -> str:
        """ReAct 推理循环"""
        # 1. think: 让 LLM 决定动作
        # 2. act: 执行工具或回复
        # 3. observe: 获取结果
        # 4. loop until done
```

### Model 继承体系

```
                 ModelWrapperBase
                      (抽象基类)
                         │
     ┌───────────┬───────┴───────┬───────────┐
     ▼           ▼                ▼           ▼
OpenAIModel  AnthropicModel  DashScopeChatModel  OllamaChatModel
```

### Java 对比

```java
// Java: 抽象类 + 接口
public abstract class AgentBase {
    protected String name;
    protected Model model;

    public abstract String reply(String message);
}

public class ReActAgent extends AgentBase {
    private List<Tool> tools;
    private Memory memory;

    @Override
    public String reply(String message) {
        // ReAct 逻辑
    }
}
```

## 5.4 模块依赖关系

```
                    ┌─────────────┐
                    │    init()   │  初始化入口
                    └──────┬──────┘
                           │
           ┌───────────────┼───────────────┐
           │               │               │
           ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │   Agent     │ │   Model     │ │    Tool     │
    │             │ │             │ │             │
    │  ─────────  │ │  ─────────  │ │  ─────────  │
    │  + model    │ │  + api_key  │ │  + func()   │
    │  + tools    │ │  + calls()  │ │  + execute()│
    │  + memory   │ │             │ │             │
    └──────┬──────┘ └─────────────┘ └─────────────┘
           │
           ▼
    ┌─────────────┐       ┌─────────────┐
    │   Memory    │◀─────▶│  Pipeline   │
    │             │       │   MsgHub    │
    └─────────────┘       └─────────────┘
           │                       ▲
           │                       │
           ▼                       │
    ┌─────────────┐               │
    │   RAG       │               │
    │  Reader/    │               │
    │  Store      │               │
    └─────────────┘               │
                                   │
    ┌─────────────┐               │
    │   Utils     │───────────────┘
    │ _utils/     │
    └─────────────┘
```

## 5.5 配置管理

### init() 函数

`agentscope.init()` 是框架的入口，类似 Spring Boot 的启动逻辑：

```python
def init(
    project: str,           # 项目名称 (必填)
    project_dir: str = "./workspace",  # 工作目录
    name: str | None = None,     # 实例名称
    
    debug: bool = False,         # 调试模式
    tracing: bool = False,       # 链路追踪
    tracing_endpoint: str | None = None,
    **kwargs
) -> None:
    # 1. 设置项目配置
    # 2. 初始化日志
    # 3. 设置 OpenTelemetry
    # 4. 连接 AgentScope Studio (可选)
```

### Java 对比

```java
// Java: @SpringBootApplication + application.yml
@SpringBootApplication
public class MyApplication {
    public static void main(String[] args) {
        SpringApplication.run(MyApplication.class, args);
    }
}

// application.yml
// spring:
//   application:
//     name: my-agent
//   main:
//     banner-mode: off
```

## 5.6 消息流

```
User Input
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Agent.receive(msg)                                     │
│  └─▶ Memory.add(msg)  ──▶ 保存到记忆                    │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Agent.think()                                          │
│  └─▶ Model.invoke(prompt)  ──▶ 调用 LLM                 │
│      └─▶ 可能触发 Tool.call()                            │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Agent.respond()                                         │
│  └─▶ Memory.add(response)  ──▶ 更新记忆                   │
└─────────────────────────────────────────────────────────┘
    │
    ▼
Response to User
```

## 5.7 扩展机制

### 注册自定义组件

AgentScope 使用**注册表模式**扩展组件：

```python
# 注册自定义 Model
from agentscope.model import model_register

@model_register("my_custom_model")
class MyCustomModel(ModelWrapperBase):
    def __init__(self, config):
        super().__init__(config)

    def __call__(self, messages):
        # 实现
        pass

# 使用
model = agentscope.model.get_model("my_custom_model", config={...})
```

### Java 对比

```java
// Java: Spring Bean 注册
@Configuration
public class MyConfig {
    @Bean
    public Model myCustomModel() {
        return new MyCustomModel();
    }
}
```


## 5.8 多智能体协作模式

AgentScope 提供多种多智能体协作模式，适用于不同场景：

### 5.8.1 Routing (路由模式)

根据输入内容自动选择最合适的 Agent：

```python
from agentscope import agent, pipeline

# 创建多个专家 Agent
researcher = agent.ReActAgent(name="研究员", model=..., tools=[tavily_search])
writer = agent.ReActAgent(name="作家", model=...)
coder = agent.ReActAgent(name="程序员", model=..., tools=[execute_python_code])

# 使用 Routing 自动选择
with pipeline.Routing(agents=[researcher, writer, coder]) as router:
    result = router("研究 AI Agent 的最新发展趋势")
    # 自动路由到最合适的 Agent
```

### 5.8.2 Handoffs (交接模式)

在多个 Agent 之间转移对话控制权：

```python
from agentscope import agent, pipeline

specialist_1 = agent.ReActAgent(name="销售", model=...)
specialist_2 = agent.ReActAgent(name="技术", model=...)

with pipeline.Handoffs(agents=[specialist_1, specialist_2]) as handoff:
    result = specialist_1("我想要了解企业版产品")
    # 自动切换到 specialist_2 继续对话
```

### 5.8.3 Supervisor (监督者模式)

监督者协调多个专家 Agent 完成任务：

```python
from agentscope import agent, pipeline

researcher = agent.ReActAgent(name="研究员", model=..., tools=[tavily_search])
writer = agent.ReActAgent(name="作家", model=...)

with pipeline.Supervisor(agents=[researcher, writer]) as supervisor:
    article = supervisor("写一篇关于 AI 在医疗领域应用的研究报告")
    # 监督者自动调度研究员和作家协作
```

### 5.8.4 Debate (辩论模式)

多个 Agent 从不同角度分析问题：

```python
from agentscope import agent, pipeline

pro_agent = agent.ReActAgent(name="正方", model=...)
con_agent = agent.ReActAgent(name="反方", model=...)

with pipeline.Debate(agents=[pro_agent, con_agent]) as debate:
    analysis = debate("分析远程办公的利弊")
```

## 5.9 Voice Agent (语音代理)

AgentScope 支持语音交互，是 2.0 时代的核心方向：

### 语音输出配置

```python
from agentscope import agent
from agentscope.model import DashScopeChatModel

# 创建带语音输出的 Agent
voice_agent = agent.ReActAgent(
    name="语音助手",
    model=DashScopeChatModel(model_name="qwen-audio"),
    tools=[...],
    speech={  # 语音配置
        "tts_api": "dashscope",  # 或 "openai", "german_tts"
        "voice": "female_2",
        "stream": True  # 流式输出
    }
)

# 对话时自动生成语音
response = voice_agent("请介绍一下你自己")
```

### TTS 支持

| TTS API | 说明 |
|---------|------|
| `dashscope` | 阿里云通义语音 |
| `openai` | OpenAI TTS |
| `german_tts` | 德国 TTS |

### Voice Agent 发展阶段

```
Phase 1: TTS (Text-to-Speech) Models
    └── 已完成：TTS 模型基类基础设施

Phase 2: Multimodal Models (Non-Realtime)
    └── 进行中：ReAct agents 多模态支持

Phase 3: Real-time Multimodal Models
    └── 规划中：实时语音交互
```

## 5.10 Agent 类型一览

| Agent 类型 | 说明 | 引入版本 |
|-----------|------|----------|
| `ReActAgent` | 核心推理 Agent | v1.0 |
| `DialogAgent` | 简单对话 Agent | v1.0 |
| `DictDialogAgent` | 字典式对话 Agent | v1.0 |
| `DeepResearchAgent` | 深度研究 Agent | v1.0.19 |
| `RealtimeAgent` | 实时语音 Agent | v2.0 (规划中) |
| `UserAgent` | 用户交互 Agent | v1.0 |
| `A2AAgent` | Agent-to-Agent 通信 | v1.0 |

## 5.11 下一步

