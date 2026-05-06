# 第五章：架构设计

## 学习目标

完成本章学习后，你将能够：

1. 理解 AgentScope 的分层模块化架构（应用层、服务层、基础设施层）
2. 掌握 Agent 继承体系（AgentBase → ReActAgentBase → ReActAgent）及其元类 Hook 机制
3. 解释 ReActAgent 的完整调用链（reply → _reasoning → _acting 循环）
4. 理解 Model、Tool、Memory、Pipeline、Formatter 各模块的职责与交互方式
5. 使用 MsgHub 和 Pipeline（sequential/fanout）实现多 Agent 协作
6. 了解各 Agent 类型（ReActAgent、UserAgent、RealtimeAgent、A2AAgent）的适用场景
7. 阅读 AgentScope 源码时知道从哪些文件入手

---

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
│   ├── _toolkit.py          # 核心管理类 Toolkit
│   ├── _types.py            # 类型定义
│   ├── _response.py         # ToolResponse 结果类
│   ├── _async_wrapper.py    # 流式响应包装器
│   ├── _coding/             # 代码执行工具 (Python, Shell)
│   ├── _text_file/          # 文件操作工具
│   ├── _multi_modality/     # 多模态工具
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

### Agent 继承体系 (源码级详解)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Agent 完整继承体系                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│                          StateModule                                     │
│                         (状态模块基类)                                     │
│                              │                                           │
│                              ▼                                           │
│                        AgentBase                                        │
│                     (异步Agent基类)                                       │
│                    使用 _AgentMeta 元类                                   │
│                    /            \           \                           │
│                   /              \           \                          │
│          ReActAgentBase      UserAgent    A2AAgent                      │
│         (ReAct模式基类)                                                    │
│     使用 _ReActAgentMeta 元类                                               │
│              /                                                                  │
│             /                                                                   │
│    ReActAgent ◄─────────────────── RealtimeAgent                           │
│   (主要推理Agent)                         (独立实现的实时Agent)                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

#### 元类与Hook机制

AgentScope 使用元类(metaclass)实现AOP风格的Hook拦截：

```python showLineNumbers
# _agent_meta.py 核心元类设计

class _AgentMeta(type):  # 第 159 行
    """包装 Agent 的 reply/observe/print 方法为 Hook 形式"""
    def __new__(mcs, name, bases, attrs):
        # 遍历需要包装的方法
        for func_name in ["reply", "print", "observe"]:
            if func_name in attrs:
                # 使用 _wrap_with_hooks 装饰
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])
        return super().__new__(mcs, name, bases, attrs)

class _ReActAgentMeta(_AgentMeta):  # 第 177 行
    """ReAct Agent 专用元类，额外包装 _reasoning 和 _acting"""
    def __new__(mcs, name, bases, attrs):
        for func_name in ["_reasoning", "_acting"]:
            if func_name in attrs:
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])
        return super().__new__(mcs, name, bases, attrs)
```

#### AgentBase 核心架构 (`_agent_base.py`)

```python
class AgentBase(StateModule, metaclass=_AgentMeta):
    """Agent 抽象基类，所有Agent的父类"""

    # 支持的 Hook 类型
    supported_hook_types = [
        "pre_reply", "post_reply",       # reply() 前后
        "pre_print", "post_print",       # print() 前后
        "pre_observe", "post_observe",   # observe() 前后
    ]

    # 类级别 Hook (所有实例共享)
    _class_pre_reply_hooks = OrderedDict()
    _class_post_reply_hooks = OrderedDict()

    # 实例级别 Hook (单个实例独享)
    _instance_pre_reply_hooks = OrderedDict()
    _instance_post_reply_hooks = OrderedDict()

    def __init__(self):  # 第 140 行
        super().__init__()
        self.id = shortuuid.uuid()  # 唯一标识
        self._reply_task = None      # 当前回复任务
        self._subscribers = {}        # 订阅者列表
        self._stream_prefix = {}      # 流式输出缓冲
```

#### ReActAgentBase 核心架构 (`_react_agent_base.py`)

```python showLineNumbers
class ReActAgentBase(AgentBase, metaclass=_ReActAgentMeta):
    """ReAct 推理模式基类"""

    # 额外的 Hook 类型
    supported_hook_types = [
        # 继承自 AgentBase
        "pre_reply", "post_reply",
        "pre_print", "post_print",
        "pre_observe", "post_observe",
        # ReAct 专用
        "pre_reasoning", "post_reasoning",  # 推理前后
        "pre_acting", "post_acting",          # 行动前后
    ]

    @abstractmethod
    async def _reasoning(self, *args, **kwargs):
        """推理过程，子类实现"""

    @abstractmethod
    async def _acting(self, *args, **kwargs):
        """行动过程，子类实现"""
```

#### ReActAgent 完整实现 (`_react_agent.py`)

```python showLineNumbers
class ReActAgent(ReActAgentBase):
    """ReAct 推理 Agent 的完整实现"""

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Toolkit | None = None,
        memory: MemoryBase | None = None,
        long_term_memory: LongTermMemoryBase | None = None,
        parallel_tool_calls: bool = False,
        max_iters: int = 10,
        # ... 更多参数
    ):
        super().__init__()

        # 核心组件
        self.model = model           # LLM
        self.formatter = formatter   # 消息格式化
        self.memory = memory        # 短期记忆
        self.long_term_memory = long_term_memory  # 长期记忆
        self.toolkit = toolkit      # 工具箱

        # 配置
        self.parallel_tool_calls = parallel_tool_calls
        self.max_iters = max_iters

    @property
    def sys_prompt(self) -> str:
        """动态系统提示词，可包含工具描述"""
        return self._sys_prompt + "\n\n" + self.toolkit.get_agent_skill_prompt()
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

```python showLineNumbers
def init(
    project: str | None = None,         # 项目名称（可选，默认自动生成）
    name: str | None = None,            # 运行实例名称
    run_id: str | None = None,          # 运行实例 ID
    logging_path: str | None = None,    # 日志保存路径
    logging_level: str = "INFO",        # 日志级别
    studio_url: str | None = None,      # AgentScope Studio 地址
    tracing_url: str | None = None,     # OpenTelemetry 追踪地址
) -> None:
    # 1. 设置项目配置（ContextVar 实现）
    # 2. 初始化日志
    # 3. 连接 AgentScope Studio（可选）
    # 4. 设置 OpenTelemetry 链路追踪（可选）
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

## 5.6 ReActAgent 完整时序图

### reply() 方法完整调用序列

```
┌─────────┐     ┌──────────┐     ┌────────┐     ┌──────────┐     ┌────────┐
│  User   │     │ ReActAgent│    │ Memory │     │ Model    │     │ Toolkit │
└────┬────┘     └─────┬────┘     └───┬────┘     └────┬─────┘     └────┬────┘
     │                │              │               │                  │
     │ reply(msg)     │              │               │                  │
     │───────────────▶│              │               │                  │
     │                │              │               │                  │
     │                │ add(msg)     │               │                  │
     │                │─────────────▶│               │                  │
     │                │              │               │                  │
     │                │ _retrieve_from_long_term_memory()              │
     │                │───────────────│───────────────│                  │
     │                │              │               │                  │
     │                │ _retrieve_from_knowledge()                   │
     │                │───────────────│───────────────────────────────│
     │                │              │               │                  │
     │                │ ┌─────────────────────────────────────────┐ │
     │                │ │  for iteration in range(max_iters):      │ │
     │                │ │                                          │ │
     │                │ │  1. _compress_memory_if_needed()         │ │
     │                │ │──────────────│                          │ │
     │                │ │              │                          │ │
     │                │ │  2. _reasoning()                       │ │
     │                │ │─────────────▶│ format()                 │ │
     │                │ │              │───────────▶│              │ │
     │                │ │              │               │ invoke()  │ │
     │                │ │              │               │──────────▶│ │
     │                │ │              │               │◀──────────│ │
     │                │ │              │◀──────────────│           │ │
     │                │ │              │               │                  │
     │                │ │  3. 检查 tool_use blocks                │ │
     │                │ │              │               │                  │
     │                │ │  4. _acting(tool_call)                  │ │
     │                │ │─────────────▶│               │ call_tool  │
     │                │ │              │               │───────────▶│ │
     │                │ │              │               │◀──────────│ │
     │                │ │              │               │                  │
     │                │ │  5. 检查完成条件                         │ │
     │                │ └─────────────────────────────────────────┘ │
     │                │              │               │                  │
     │                │ record()     │               │                  │
     │                │◀─────────────│               │                  │
     │                │              │               │                  │
     │ reply_msg     │              │               │                  │
     │◀──────────────│              │               │                  │
     │                │              │               │                  │
```

### _reasoning() 详细流程

```
┌──────────────┐    ┌────────────┐    ┌───────────┐    ┌──────────┐
│ ReActAgent  │    │  Memory    │    │ Formatter │    │  Model   │
└──────┬──────┘    └─────┬──────┘    └─────┬─────┘    └────┬─────┘
       │                 │                  │                 │
       │ _reasoning()   │                  │                 │
       │────────────────│                  │                 │
       │                 │                  │                 │
       │                 │ get_memory()    │                 │
       │                 │◀──────────────│                 │
       │                 │────────────────│                 │
       │                 │                  │                 │
       │                 │ format(msgs)    │                 │
       │                 │─────────────────▶│                │
       │                 │                  │                 │
       │                 │                  │ model(prompt)  │
       │                 │                  │────────────────▶
       │                 │                  │                 │
       │                 │                  │◀────────────────│
       │                 │                  │                 │
       │                 │ add(msg)         │                 │
       │                 │◀─────────────────│                 │
       │                 │                  │                 │
       │ return msg     │                  │                 │
       │◀──────────────│                  │                 │
```

### _acting() 详细流程

```
┌──────────────┐    ┌────────────┐    ┌───────────┐    ┌──────────┐
│ ReActAgent  │    │   Memory   │    │  Toolkit  │    │   Tool   │
└──────┬──────┘    └─────┬──────┘    └─────┬─────┘    └────┬─────┘
       │                 │                  │                 │
       │ _acting(tool_call)                │                 │
       │────────────────│                  │                 │
       │                 │                  │                 │
       │                 │ call_tool_function(tool_call)     │
       │                 │──────────────────▶│                │
       │                 │                  │                 │
       │                 │                  │ execute()      │
       │                 │                  │───────────────▶│
       │                 │                  │                 │
       │                 │                  │◀───────────────│
       │                 │                  │                 │
       │                 │ add(tool_result) │                 │
       │                 │◀─────────────────│                 │
       │                 │                  │                 │
       │ return output  │                  │                 │
       │◀──────────────│                  │                 │
```

## 5.7 消息流 (简化版)

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

## 5.8 各种 Agent 类型详解

### UserAgent - 用户输入处理

`UserAgent` 用于获取用户输入，是人机交互的桥梁：

```python
# _user_agent.py 核心实现
class UserAgent(AgentBase):
    """用户交互 Agent"""

    _input_method: UserInputBase = TerminalUserInput()

    def __init__(self, name: str):
        super().__init__()
        self.name = name

    async def reply(
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        # 调用输入方法获取用户输入
        input_data = await self._input_method(
            agent_id=self.id,
            agent_name=self.name,
            structured_model=structured_model,
        )
        # 构建消息
        msg = Msg(
            self.name,
            content=input_data.blocks_input,
            role="user",
            metadata=input_data.structured_input,
        )
        await self.print(msg)
        return msg
```

**使用场景：**
- 等待用户确认或输入
- 结构化数据收集（如表单）
- 对话式交互入口

### A2AAgent - Agent间通信

`A2AAgent` 实现 A2A (Agent-to-Agent) 协议，用于与外部 Agent 系统互操作：

```python
# _a2a_agent.py 核心实现
class A2AAgent(AgentBase):
    """A2A 协议 Agent"""

    def __init__(
        self,
        agent_card: AgentCard,  # 远程 Agent 信息
        client_config: ClientConfig | None = None,
        consumers: list[Consumer] | None = None,
    ):
        super().__init__()
        self.agent_card = agent_card
        self._a2a_client_factory = ClientFactory(config=client_config)
        self._observed_msgs: list[Msg] = []
        self.formatter = A2AChatFormatter()

    async def reply(self, msg: Msg | list[Msg] | None = None, **kwargs) -> Msg:
        # 合并观察到的消息
        msgs_list = self._observed_msgs
        if msg:
            msgs_list.extend(msg if isinstance(msg, list) else [msg])

        # 创建客户端并发送
        client = self._a2a_client_factory.create(card=self.agent_card)
        a2a_message = await self.formatter.format(msgs_list)

        # 处理响应
        async for item in client.send_message(a2a_message):
            # 转换 A2A 消息为 AgentScope 格式
            response_msg = await self.formatter.format_a2a_message(...)

        self._observed_msgs.clear()
        return response_msg
```

**A2A 协议特点：**
- 基于 HTTP 的请求-响应协议
- 支持流式响应
- 任务状态跟踪
- 工件(Artifact)传递

### RealtimeAgent - 实时语音交互

`RealtimeAgent` 专为实时语音交互设计，使用事件驱动架构：

```python
# _realtime_agent.py 核心实现
class RealtimeAgent(StateModule):
    """实时语音 Agent"""

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model: RealtimeModelBase,  # 实时模型，如 qwen-omni
        toolkit: Toolkit | None = None,
    ):
        super().__init__()
        self.model = model
        self._incoming_queue = Queue()   # 输入队列
        self._model_response_queue = Queue()  # 输出队列

    async def start(self, outgoing_queue: Queue) -> None:
        # 连接实时模型
        await self.model.connect(
            self._model_response_queue,
            instructions=self.sys_prompt,
            tools=self.toolkit.get_json_schemas() if self.toolkit else None,
        )
        # 启动事件循环
        self._external_event_handling_task = asyncio.create_task(
            self._forward_loop()  # 外部事件 -> 模型
        )
        self._model_response_handling_task = asyncio.create_task(
            self._model_response_loop(outgoing_queue)  # 模型 -> 外部
        )
```

**事件处理流程：**

```
外部输入                    RealtimeAgent                  实时模型
   │                            │                            │
   │ ClientAudioAppendEvent     │                            │
   │──────────────────────────▶│                            │
   │                            │                            │
   │                            │ AudioBlock                 │
   │                            │───────────────────────────▶│
   │                            │                            │
   │                            │◀──────────────────────────│
   │                            │ ModelResponseAudioDelta   │
   │                            │                            │
   │ AgentResponseAudioDelta   │                            │
   │◀──────────────────────────│                            │
   │                            │                            │
```

## 5.9 扩展机制

### 注册自定义组件

AgentScope 支持通过直接继承和实例化扩展组件：

```python
# 创建自定义 Model
from agentscope.model import ChatModelBase

class MyCustomModel(ChatModelBase):
    def __init__(self, model_name: str, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self.model_name = model_name
        self.api_key = api_key

    async def __call__(self, messages, **kwargs):
        # 实现具体 API 调用
        pass

# 使用 - 直接实例化
model = MyCustomModel(model_name="my-model", api_key="sk-xxx")
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


## 5.14 源码层级详解

### 源码文件位置索引

```
src/agentscope/
├── __init__.py                          # 入口文件 (init 函数)
├── _version.py                          # 版本信息
├── _logging.py                          # 日志配置
├── _run_config.py                       # 运行时配置
├── _utils/                              # 工具函数集
│
├── agent/                               # Agent 模块
│   ├── _agent_base.py                   # AgentBase 基类 (774行)
│   ├── _agent_meta.py                   # 元类实现 (Hook机制)
│   ├── _react_agent.py                  # ReActAgent 实现 (主Agent)
│   ├── _react_agent_base.py             # ReActAgentBase 基类
│   ├── _user_agent.py                   # UserAgent 实现
│   ├── _realtime_agent.py               # 实时语音Agent
│   ├── _a2a_agent.py                    # A2A协议Agent
│   ├── _user_input.py                   # 用户输入处理
│   └── _chat_room.py                    # 聊天室实现
│
├── model/                               # 模型模块
│   ├── _model_base.py                    # 模型基类
│   ├── _model_response.py               # 模型响应结构
│   ├── _openai_model.py                 # OpenAI 模型实现
│   ├── _anthropic_model.py              # Claude 模型实现
│   ├── _dashscope_model.py              # 通义模型实现
│   ├── _gemini_model.py                 # Gemini 模型实现
│   ├── _ollama_model.py                 # Ollama 本地模型
│   └── _trinity_model.py                # Trinity 模型实现
│
├── pipeline/                            # 管道模块
│   ├── _msghub.py                       # MsgHub 消息中心
│   ├── _functional.py                   # 函数式管道 (sequential/fanout)
│   └── _class.py                        # Pipeline 类封装
│
├── memory/                              # 记忆模块
│   ├── _memory_base.py                  # 记忆基类
│   ├── _working_memory/                 # 短期记忆
│   └── _long_term_memory/               # 长期记忆
│
├── tool/                                # 工具模块
│   ├── _tool_base.py                    # 工具基类
│   ├── _coding.py                       # 代码执行工具
│   ├── _text_file.py                    # 文件操作工具
│   └── _mcp.py                          # MCP 协议工具
│
├── message/                             # 消息模块
│   └── Msg.py                           # 消息结构定义
│
├── formatter/                           # 格式化器模块
│
├── rag/                                 # RAG 模块
│
├── session/                             # 会话管理
│
├── tracing/                             # 链路追踪
│
└── hooks/                               # Hook 系统
    └── _hooks.py                        # Hook 实现
```

### 5.14.1 init() 函数源码分析

`init()` 函数是 AgentScope 框架的入口点，类似 Spring Boot 的启动逻辑。以下是完整源码分析：

```python
# __init__.py 第 72-157 行
def init(
    project: str | None = None,
    name: str | None = None,
    run_id: str | None = None,
    logging_path: str | None = None,
    logging_level: str = "INFO",
    studio_url: str | None = None,
    tracing_url: str | None = None,
) -> None:
    """初始化 AgentScope 框架"""
```

**源码执行流程：**

```
┌─────────────────────────────────────────────────────────────┐
│                     init() 函数执行流程                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. 配置更新 (第 106-113 行)                                  │
│     ┌─────────────────────────────────────────────┐         │
│     │  if project: _config.project = project      │         │
│     │  if name:   _config.name = name              │         │
│     │  if run_id: _config.run_id = run_id         │         │
│     └─────────────────────────────────────────────┘         │
│                          ↓                                   │
│  2. 日志初始化 (第 115 行)                                    │
│     ┌─────────────────────────────────────────────┐         │
│     │  setup_logger(logging_level, logging_path)  │         │
│     └─────────────────────────────────────────────┘         │
│                          ↓                                   │
│  3. Studio 连接 (第 117-145 行)                               │
│     ┌─────────────────────────────────────────────┐         │
│     │  requests.post(...)  注册运行实例            │         │
│     │  UserAgent.override_input_method(...)      │         │
│     │  _equip_as_studio_hooks(studio_url)        │         │
│     └─────────────────────────────────────────────┘         │
│                          ↓                                   │
│  4. 链路追踪 (第 147-156 行)                                  │
│     ┌─────────────────────────────────────────────┐         │
│     │  setup_tracing(endpoint)                    │         │
│     │  _config.trace_enabled = True               │         │
│     └─────────────────────────────────────────────┘         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**全局配置实例 (第 22-41 行)：**

```python
# 线程和异步安全的全局配置实例
_config = _ConfigCls(
    run_id=ContextVar("run_id", default=shortuuid.uuid()),
    project=ContextVar("project", default="UnnamedProject_At" + ...),
    name=ContextVar("name", default=datetime.now().strftime("%H%M%S_") + ...),
    created_at=ContextVar("created_at", default=datetime.now().strftime(...)),
    trace_enabled=ContextVar("trace_enabled", default=False),
)
```

**关键设计特点：**
- 使用 `ContextVar` 实现线程/协程安全的上下文隔离
- 每个异步任务可以独立设置自己的 project/name/run_id
- 配置延迟初始化，默认值在模块加载时生成

### 5.14.2 分层架构源码交互关系

#### 应用层 → 服务层 → 基础设施层

```
┌─────────────────────────────────────────────────────────────────┐
│                     用户代码 (examples/)                         │
│   main.py  ──▶  agentscope.init()  ──▶  ReActAgent(...)        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                     src/agentscope/__init__.py                   │
│                         init() 函数                               │
│    ┌──────────────────────────────────────────────────────────┐ │
│    │  setup_logger() → _logging.py                            │ │
│    │  setup_tracing() → tracing/                              │ │
│    │  StudioUserInput → agent/_user_input.py                  │ │
│    └──────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      Service Layer (服务层)                       │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐│
│  │  Agent  │  │  Model  │  │  Tool   │  │ Memory  │  │Pipeline ││
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘│
│       │            │            │            │            │       │
│       ↓            ↓            ↓            ↓            ↓       │
│  agent/         model/        tool/       memory/      pipeline/   │
│  _agent_base   _model_base   _tool_base  _memory_base  _msghub    │
│  _react_agent  _openai_model _coding     _working_mem  _functional│
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  Infrastructure Layer (基础设施层)                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │requests  │  │ asyncio  │  │shortuuid │  │  redis   │        │
│  │(HTTP)    │  │(异步IO)  │  │(ID生成)  │  │(可选)    │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
└─────────────────────────────────────────────────────────────────┘
```

#### 核心模块源码交互时序

```
用户代码                    AgentScope 框架                         外部服务
    │                            │                                    │
    │ agentscope.init(...)       │                                    │
    │───────────────────────────▶│                                    │
    │                            │                                    │
    │ ReActAgent(...)           │                                    │
    │───────────────────────────▶│                                    │
    │                            │                                    │
    │ agent.reply(msg)          │                                    │
    │───────────────────────────▶│                                    │
    │                            │                                    │
    │                            │ _reasoning()                       │
    │                            │──────────▶ Formatter.format()       │
    │                            │◀───────────                        │
    │                            │                                    │
    │                            │ model.invoke(prompt)               │
    │                            │───────────────────────────────────▶│ OpenAI API
    │                            │◀──────────────────────────────────│
    │                            │                                    │
    │                            │ 检查 tool_use blocks               │
    │                            │                                    │
    │                            │ _acting(tool_call)                  │
    │                            │──────────▶ Toolkit.call_tool()     │
    │                            │◀───────────                        │
    │                            │                                    │
    │                            │ Memory.record()                   │
    │                            │──────────▶ memory/                 │
    │                            │                                    │
    │ reply_msg                  │                                    │
    │◀───────────────────────────│                                    │
```

### 5.14.3 AgentBase 核心架构源码分析

```python showLineNumbers
# _agent_base.py 第 30-184 行
class AgentBase(StateModule, metaclass=_AgentMeta):
    """异步 Agent 基类"""

    # 支持的 Hook 类型 (第 36-43 行)
    supported_hook_types = [
        "pre_reply", "post_reply",       # reply() 前后
        "pre_print", "post_print",       # print() 前后
        "pre_observe", "post_observe",   # observe() 前后
    ]

    # 类级别 Hook (所有实例共享) (第 46-79 行)
    _class_pre_reply_hooks = OrderedDict()
    _class_post_reply_hooks = OrderedDict()
    _class_pre_print_hooks = OrderedDict()
    _class_post_print_hooks = OrderedDict()
    _class_pre_observe_hooks = OrderedDict()
    _class_post_observe_hooks = OrderedDict()

    # 实例级别 Hook (单个实例独享) (第 151-158 行)
    def __init__(self) -> None:
        self._instance_pre_reply_hooks = OrderedDict()
        self._instance_post_reply_hooks = OrderedDict()
        self._instance_pre_observe_hooks = OrderedDict()
        self._instance_post_observe_hooks = OrderedDict()

        # 订阅者列表 (第 168 行)
        self._subscribers: dict[str, list[AgentBase]] = {}
```

#### __call__ 方法核心流程 (第 448-467 行)

```python showLineNumbers
async def __call__(self, *args: Any, **kwargs: Any) -> Msg:
    """调用 reply 函数并处理广播"""
    self._reply_id = shortuuid.uuid()

    reply_msg: Msg | None = None
    try:
        self._reply_task = asyncio.current_task()
        reply_msg = await self.reply(*args, **kwargs)

    # 处理中断 (用户取消)
    except asyncio.CancelledError:
        reply_msg = await self.handle_interrupt(*args, **kwargs)

    finally:
        # 广播回复消息给所有订阅者
        if reply_msg:
            await self._broadcast_to_subscribers(reply_msg)
        self._reply_task = None

    return reply_msg
```

#### 消息广播机制 (第 469-485 行)

```python showLineNumbers
async def _broadcast_to_subscribers(
    self,
    msg: Msg | list[Msg] | None,
) -> None:
    """将消息广播给所有订阅者"""
    if msg is None:
        return

    # 发送前移除思考块 (内部推理不对外可见)
    broadcast_msg = self._strip_thinking_blocks(msg)

    for subscribers in self._subscribers.values():
        for subscriber in subscribers:
            await subscriber.observe(broadcast_msg)
```

### 5.14.4 ReActAgent 核心调用链分析

```python
# _react_agent.py 第 98 行
class ReActAgent(ReActAgentBase):
    """ReAct 推理 Agent 的完整实现"""

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Toolkit | None = None,
        memory: MemoryBase | None = None,
        # ...
    ):
        self.model = model           # LLM
        self.formatter = formatter   # 消息格式化
        self.memory = memory        # 短期记忆
        self.toolkit = toolkit      # 工具箱
```

#### reply() 方法完整调用序列

```python showLineNumbers
# _react_agent.py 第 376 行
async def reply(
    self,
    msg: Msg | list[Msg] | None = None,
    # ...
) -> Msg:
    # 1. 添加用户消息到记忆 (第 305-318 行)
    if msg:
        msg = msg if isinstance(msg, list) else [msg]
        for m in msg:
            self.memory.add(m)

    # 2. 检索长期记忆 (第 321-340 行)
    context_from_ltm = await self._retrieve_from_long_term_memory()

    # 3. ReAct 迭代循环 (第 343-432 行)
    for iteration in range(self.max_iters):
        # 3.1 压缩记忆 (第 350-365 行)
        await self._compress_memory_if_needed()

        # 3.2 推理阶段 (第 371-390 行)
        reasoning_result = await self._reasoning(
            inner_memory=inner_memory,
            # ...
        )

        # 3.3 检查是否使用工具 (第 393-410 行)
        if has_tool_calls:
            # 3.4 执行工具 (第 415-430 行)
            tool_result = await self._acting(
                tool_calls=tool_calls,
                # ...
            )
            inner_memory.add(tool_result)
        else:
            # 无工具，直接返回
            return reasoning_result

    # 4. 记录到记忆 (第 478 行)
    await self.memory.add(reply_msg)
    return reply_msg
```

#### _reasoning() 详细流程

```python
# _react_agent.py 第 540 行
async def _reasoning(self, inner_memory: MemoryBase, ...) -> Msg:
    # 1. 获取记忆上下文 (第 520-540 行)
    memory_prompt = inner_memory.get_memory()

    # 2. 格式化消息 (第 545-560 行)
    formatted_messages = self.formatter.format(
        inner_memory.get_memory()
    )

    # 3. 调用模型 (第 565-590 行)
    # 注意: stream 是模型属性，不是参数
    res = await self.model(
        formatted_messages,
        tools=self.toolkit.get_json_schemas(),
        tool_choice=tool_choice,
    )
    # 如果 self.model.stream = True，则 res 是 AsyncGenerator
    # 如果 self.model.stream = False，则 res 是 ChatResponse

    # 4. 解析响应 (第 595-600 行)
    return self._parse_model_response(response)
```

### 5.14.5 Pipeline 管道源码分析

#### sequential_pipeline 顺序执行

```python showLineNumbers
# pipeline/_functional.py 第 10-44 行
async def sequential_pipeline(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
) -> Msg | list[Msg] | None:
    """顺序执行: Agent1 -> Agent2 -> Agent3"""
    for agent in agents:
        msg = await agent(msg)  # 上一个输出作为下一个输入
    return msg
```

#### fanout_pipeline 并行分发

```python showLineNumbers
# pipeline/_functional.py 第 47-104 行
async def fanout_pipeline(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
    enable_gather: bool = True,
    **kwargs: Any,
) -> list[Msg]:
    """并行执行: 同一消息分发给所有 Agent"""
    if enable_gather:
        # 并发执行 (第 97-102 行)
        tasks = [
            asyncio.create_task(agent(deepcopy(msg), **kwargs))
            for agent in agents
        ]
        return await asyncio.gather(*tasks)
    else:
        # 顺序执行 (第 104 行)
        return [await agent(deepcopy(msg), **kwargs) for agent in agents]
```

#### MsgHub 消息中心

```python showLineNumbers
# pipeline/_msghub.py 第 14-157 行
class MsgHub:
    """MsgHub 消息订阅与广播中心"""

    def __init__(
        self,
        participants: Sequence[AgentBase],
        announcement: list[Msg] | Msg | None = None,
        enable_auto_broadcast: bool = True,
        name: str | None = None,
    ):
        self.participants = list(participants)
        self.enable_auto_broadcast = enable_auto_broadcast

    async def __aenter__(self) -> "MsgHub":
        """进入上下文管理器时重置订阅者"""
        self._reset_subscriber()
        if self.announcement is not None:
            await self.broadcast(msg=self.announcement)
        return self

    async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
        """退出时清理订阅者"""
        if self.enable_auto_broadcast:
            for agent in self.participants:
                agent.remove_subscribers(self.name)

    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        """广播消息给所有参与者"""
        for agent in self.participants:
            await agent.observe(msg)
```

### 5.14.6 核心调用链完整视图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         用户代码调用链                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  agentscope.init(project="my_project")                                   │
│       │                                                                 │
│       ├──▶ _config.project = "my_project"  (全局配置)                    │
│       ├──▶ setup_logger(logging_level, logging_path)  → _logging.py      │
│       ├──▶ requests.post(...)  注册到 Studio (可选)                      │
│       └──▶ setup_tracing(endpoint)  → tracing/                          │
│                                                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  agent = ReActAgent(name="assistant", model=my_model, ...)               │
│       │                                                                 │
│       └──▶ __init__ 中初始化组件:                                        │
│            ├── self.model = model                                        │
│            ├── self.formatter = formatter                                │
│            ├── self.memory = memory                                      │
│            └── self.toolkit = toolkit                                    │
│                                                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  response = await agent("你好")                                          │
│       │                                                                 │
│       ├──▶ __call__(msg)  (_agent_base.py:448)                          │
│       │       │                                                          │
│       │       └──▶ self.reply(msg)  → ReActAgent.reply()                │
│       │               │                                                  │
│       │               ├──▶ memory.add(msg)                               │
│       │               │                                                  │
│       │               ├──▶ for i in range(max_iters):                    │
│       │               │       │                                          │
│       │               │       ├──▶ _reasoning()                          │
│       │               │       │       │                                  │
│       │               │       │       ├──▶ memory.get_memory()           │
│       │               │       │       ├──▶ formatter.format()            │
│       │               │       │       └──▶ model(response)               │
│       │               │       │                                              │
│       │               │       └──▶ 检查 tool_use blocks                   │
│       │               │               │                                  │
│       │               │               └──▶ _acting(tool_calls)            │
│       │               │                       │                          │
│       │               │                       └──▶ toolkit.call_tool()    │
│       │               │                                                      │
│       │               └──▶ memory.add(response)                            │
│       │                                                                          │
│       └──▶ _broadcast_to_subscribers(response)                             │
│               │                                                              │
│               └──▶ subscriber.observe(response)  → MsgHub 广播              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.14.7 架构流程图

#### AgentScope 初始化流程

```
┌──────────────────────────────────────────────────────────────────┐
│                     agentscope.init()                             │
└────────────────────────────┬─────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  1. 配置更新     │ │  2. 日志初始化   │ │  3. Studio连接  │
│                 │ │                 │ │                 │
│ _config.project │ │ setup_logger()  │ │ requests.post() │
│ _config.name    │ │    ↓           │ │ UserAgent.      │
│ _config.run_id  │ │ _logging.py    │ │ override_input()│
└─────────────────┘ └─────────────────┘ └────────┬────────┘
                                                 │
                                                 ▼
                                    ┌─────────────────────────┐
                                    │  4. 链路追踪设置        │
                                    │                        │
                                    │ setup_tracing(endpoint)│
                                    │ _config.trace_enabled  │
                                    └─────────────────────────┘
```

#### ReActAgent 运行时交互图

```
┌──────────────────────────────────────────────────────────────────────┐
│                           ReActAgent                                  │
│                                                                      │
│  ┌────────────┐     ┌────────────┐     ┌────────────┐              │
│  │  Memory    │◀───▶│  Formatter │◀───▶│   Model    │              │
│  │ (记忆)     │     │  (格式化)   │     │   (LLM)    │              │
│  └─────┬──────┘     └────────────┘     └──────┬─────┘              │
│        │                                        │                     │
│        │              ┌────────────┐            │                     │
│        └─────────────▶│   Toolkit  │◀───────────┘                     │
│                       │  (工具箱)   │                                 │
│                       └──────┬─────┘                                 │
│                              │                                        │
│                              ▼                                        │
│                       ┌────────────┐                                 │
│                       │   Tool     │                                 │
│                       │  (具体工具) │                                 │
│                       └────────────┘                                 │
│                                                                      │
│  用户输入 ──▶ reply() ──▶ 推理循环 ──▶ 返回结果                       │
└──────────────────────────────────────────────────────────────────────┘
```

#### 多 Agent 协作时序图

```
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
│  User   │     │ Agent1  │     │ MsgHub  │     │ Agent2  │
└────┬────┘     └────┬────┘     └────┬────┘     └────┬────┘
     │               │               │               │
     │ agent1(msg)   │               │               │
     │──────────────▶│               │               │
     │               │               │               │
     │               │ reply()       │               │
     │               │──────────┐    │               │
     │               │           │    │               │
     │               │◀──────────┘    │               │
     │               │               │               │
     │               │ broadcast()   │               │
     │               │──────────────▶│               │
     │               │               │               │
     │               │               │ observe()    │
     │               │               │─────────────▶│
     │               │               │               │
     │ response      │               │               │
     │◀──────────────│               │               │
     │               │               │               │
```

### 5.14.8 源码行号索引

> **注意**: 以下行号基于 v1.0.19 版本，仅供参考。不同版本可能有所变动。

| 模块 | 文件 | 核心类和函数 | 行号 |
|------|------|-------------|------|
| **入口** | `__init__.py` | `init()` | 72-157 |
| | | `_config` 全局配置 | 22-41 |
| **Agent** | `agent/_agent_base.py` | `AgentBase` 类 | 30-184 |
| | | `__call__()` | 448-467 |
| | | `_broadcast_to_subscribers()` | 469-485 |
| | `agent/_react_agent.py` | `ReActAgent` 类 | 98 |
| | | `reply()` | 376 |
| | | `_reasoning()` | 540 |
| | `agent/_agent_meta.py` | `_AgentMeta` 元类 | - |
| **Pipeline** | `pipeline/_msghub.py` | `MsgHub` 类 | 14-157 |
| | | `broadcast()` | 130-138 |
| | `pipeline/_functional.py` | `sequential_pipeline()` | 10-44 |
| | | `fanout_pipeline()` | 47-104 |
| **Model** | `model/_model_base.py` | `ChatModelBase` | - |
| | `model/_openai_model.py` | `OpenAIChatModel` | - |
| **Memory** | `memory/_memory_base.py` | `MemoryBase` | - |
| **Tool** | `tool/_tool_base.py` | `ToolBase` | - |

### 5.14.9 源码阅读建议

1. **入口点**: 先读 `__init__.py` 的 `init()` 函数，理解框架初始化流程
2. **Agent 核心**: 阅读 `_agent_base.py` 理解 Hook 机制和消息广播
3. **业务逻辑**: 阅读 `_react_agent.py` 理解 ReAct 推理循环
4. **协作模式**: 阅读 `_msghub.py` 和 `_functional.py` 理解多 Agent 协作
5. **模型抽象**: 阅读 `model/_model_base.py` 理解模型调用抽象

## 5.10 多智能体协作模式

AgentScope 提供强大的多智能体协作机制，核心是 **MsgHub（消息中心）** 配合 **Pipeline（管道）**。以下是详细分析：

### 5.10.1 MsgHub 消息中心

MsgHub 是 AgentScope 多智能体协作的核心组件，负责管理 Agent 之间的消息订阅与广播。

#### 核心原理

```
┌─────────────────────────────────────────────────────────────────┐
│                         MsgHub                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  participants: [Agent1, Agent2, Agent3, ...]            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                   │
│          ┌───────────────────┼───────────────────┐             │
│          ▼                   ▼                   ▼             │
│     ┌─────────┐         ┌─────────┐         ┌─────────┐        │
│     │ Agent1  │         │ Agent2  │         │ Agent3  │        │
│     └────┬────┘         └────┬────┘         └────┬────┘        │
│          │                   │                   │             │
│          └───────────────────┼───────────────────┘             │
│                              ▼                                   │
│                   ┌──────────────────┐                          │
│                   │  广播消息给所有   │                          │
│                   │  其他参与者      │                          │
│                   └──────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
```

#### 订阅者机制

每个 Agent 维护一个 `_subscribers` 字典，key 是 MsgHub 的 name，value 是订阅该 Agent 消息的 Agent 列表。

```python showLineNumbers
# AgentBase 中的订阅者管理 (_agent_base.py)
self._subscribers: dict[str, list[AgentBase]] = {}

def reset_subscribers(self, msghub_name: str, subscribers: list["AgentBase"]) -> None:
    """重置订阅者列表，排除自身"""
    self._subscribers[msghub_name] = [_ for _ in subscribers if _ != self]

def remove_subscribers(self, msghub_name: str) -> None:
    """移除特定 MsgHub 的订阅者"""
    self._subscribers.pop(msghub_name, None)
```

#### 消息广播流程

```python showLineNumbers
# AgentBase 中的广播逻辑
async def _broadcast_to_subscribers(self, reply_msg: Msg) -> None:
    """将回复消息广播给所有订阅者"""
    for subscribers in self._subscribers.values():
        for subscriber in subscribers:
            await subscriber.observe(broadcast_msg)
```

#### 基本用法

```python showLineNumbers
from agentscope.pipeline import MsgHub
from agentscope.message import Msg

async with MsgHub(participants=[agent1, agent2, agent3]) as hub:
    # 进入时广播 announcement 消息（如果指定）
    await hub.broadcast(Msg("system", "开始讨论", "system"))

    # Agent1 的回复会自动广播给 agent2 和 agent3
    result1 = await agent1(user_msg)

    # 手动广播消息
    await hub.broadcast(Msg("system", "讨论结束", "system"))

# 退出时自动清理订阅者
```

#### 动态管理参与者

```python
hub = MsgHub(participants=[alice, bob, charlie])

# 添加参与者
hub.add(new_agent)

# 删除参与者
hub.delete(bob)

# 动态开启/关闭自动广播
hub.set_auto_broadcast(False)  # 关闭后只支持手动 broadcast()
```

#### 带初始公告的多参与者对话

```python
# examples/workflows/multiagent_conversation/main.py
async with MsgHub(
    participants=[alice, bob, charlie],
    announcement=Msg("system", "现在进行自我介绍...", "system"),
) as hub:
    await sequential_pipeline([alice, bob, charlie])

    # 模拟 Bob 离开
    hub.delete(bob)
    await hub.broadcast(Msg("bob", "我先走了，再见！", "assistant"))
```

---

### 5.10.2 Pipeline 管道

Pipeline 提供了两种核心模式：**顺序执行** 和 **并行分发**。

#### SequentialPipeline 顺序管道

```python showLineNumbers
# 顺序执行: Agent1 -> Agent2 -> Agent3
from agentscope.pipeline import sequential_pipeline, SequentialPipeline

# 函数式调用
result = await sequential_pipeline([agent1, agent2, agent3], user_msg)

# 类方式调用（可复用）
pipeline = SequentialPipeline([agent1, agent2, agent3])
result = await pipeline(user_msg)
```

#### FanoutPipeline 并行管道

```python showLineNumbers
# 并行执行: 同一消息分发给所有 Agent
from agentscope.pipeline import fanout_pipeline, FanoutPipeline

# 并发执行（默认）
results = await fanout_pipeline([agent1, agent2, agent3], user_msg)

# 顺序执行
results = await fanout_pipeline(
    [agent1, agent2, agent3],
    user_msg,
    enable_gather=False
)

# 类方式调用
pipeline = FanoutPipeline([alice, bob, charlie], enable_gather=True)
results = await pipeline(user_msg)
```

#### 实际应用示例

```python showLineNumbers
# examples/workflows/multiagent_concurrent/main.py
class ExampleAgent(AgentBase):
    async def reply(self, *args, **kwargs) -> Msg:
        start_time = datetime.now()
        await self.print(Msg(self.name, f"begins at {start_time}", "assistant"))
        await asyncio.sleep(np.random.choice([2, 3, 4]))
        end_time = datetime.now()
        return Msg(self.name, f"finishes at {end_time}", "user")

# 并发执行：总耗时约 4 秒（最慢的那个）
collected_res = await fanout_pipeline([alice, bob, chalice], enable_gather=True)

# 顺序执行：总耗时约 9 秒（求和）
collected_res = await fanout_pipeline([alice, bob, chalice], enable_gather=False)
```

---

### 5.10.3 Pipeline 组合与嵌套

多种管道可以嵌套使用，实现复杂的工作流：

#### 嵌套示例：辩论赛

```python showLineNumbers
# examples/workflows/multiagent_debate/main.py
async def run_multiagent_debate():
    while True:
        # 1. MsgHub 内进行辩论（Agent 互相听到对方观点）
        async with MsgHub(participants=[alice, bob, moderator]):
            await alice(Msg("user", "正方请发表观点", "user"))
            await bob(Msg("user", "反方请发表观点", "user"))

        # 2. 主持人（moderator）在 MsgHub 外独立评判
        msg_judge = await moderator(
            Msg("user", "辩论是否结束？正确答案是什么？", "user"),
            structured_model=JudgeModel,
        )

        if msg_judge.metadata.get("finished"):
            print(f"正确答案: {msg_judge.metadata.get('correct_answer')}")
            break
```

#### 复杂嵌套：狼人杀游戏

```python
# examples/game/werewolves/game.py
async def werewolves_game(agents: list[ReActAgent]):
    players = Players()

    # 第一阶段：广播游戏开始
    async with MsgHub(participants=agents) as greeting_hub:
        await greeting_hub.broadcast(
            await moderator(Prompts.to_all_new_game.format(names_to_str(agents)))
        )

    # 分配角色...

    # 夜晚阶段：狼人讨论（子集 MsgHub）
    async with MsgHub(participants=werewolves) as night_hub:
        await werewolves_pipeline ...

    # 投票阶段：全体讨论
    async with MsgHub(participants=alive_players) as vote_hub:
        await fanout_pipeline(alive_players, vote_prompt)
```

#### 流式消息收集

```python
from agentscope.pipeline import stream_printing_messages, sequential_pipeline

# 收集多个 Agent 的流式输出
async for msg, is_last in stream_printing_messages(
    agents=[agent1, agent2],
    coroutine_task=sequential_pipeline([agent1, agent2], user_msg),
):
    print(f"收到消息: {msg.content}, is_last={is_last}")
```

---

### 5.10.4 协作模式实践

#### 模式一：广播讨论（MsgHub）

适用场景：多人讨论、会议、头脑风暴

```python
# 所有参与者都能看到彼此的消息
async with MsgHub(participants=[alice, bob, charlie, david]) as hub:
    for _ in range(3):  # 3 轮讨论
        for agent in [alice, bob, charlie, david]:
            await agent(get_next_topic())
```

#### 模式二：流水线处理（SequentialPipeline）

适用场景：多步骤处理、管道式工作流

```python
# 研究 -> 写作 -> 审核 的完整流程
research_pipeline = SequentialPipeline([researcher, writer, reviewer])
article = await research_pipeline("AI 在医疗领域的应用")
```

#### 模式三：并行分发 + 聚合（FanoutPipeline + 聚合）

适用场景：征求意见、方案对比、投票

```python
# 并行收集意见
opinions = await fanout_pipeline([expert1, expert2, expert3, expert4], topic)

# 聚合意见
aggregated = await aggregator("\n".join([o.content for o in opinions]))
```

#### 模式四：辩论模式（MsgHub + 评判 Agent）

适用场景：决策分析、方案评审

```python
# 主持人的评判逻辑
class JudgeModel(BaseModel):
    finished: bool
    decision: str | None
    confidence: float

# 辩论循环
while True:
    async with MsgHub(participants=[pro_agent, con_agent]):
        await pro_agent(topic)
        await con_agent(topic)  # 看到 pro 的观点

    verdict = await moderator(
        Msg("user", "判断辩论是否结束", "user"),
        structured_model=JudgeModel
    )

    if verdict.metadata.get("finished"):
        break
```

---

### 5.10.5 分布式部署注意事项

在分布式环境下部署多 Agent 协作系统时，需要注意以下问题：

#### 1. 消息传递

| 问题 | 本地模式 | 分布式模式 |
|-----|---------|-----------|
| 消息广播 | 内存传递 | 需要序列化/反序列化 |
| 延迟 | 低（<1ms） | 高（取决于网络） |
| 可靠性 | 进程内保证 | 需要确认机制 |

#### 2. MsgHub 分布式改造

```python
# 本地 MsgHub（单进程）
class MsgHub:
    async def broadcast(self, msg: Msg) -> None:
        for agent in self.participants:
            await agent.observe(msg)

# 分布式 MsgHub 需要考虑：
# 1. 消息序列化（JSON/ pickle）
# 2. 网络传输（HTTP/WebSocket/gRPC）
# 3. 参与者发现（服务注册中心）
# 4. 消息确认与重试
```

#### 3. Pipeline 分布式执行

```python
# 本地并行执行
await fanout_pipeline([a1, a2, a3], msg)  # asyncio.gather

# 分布式执行需要：
# 1. 任务队列（Celery/Redis Queue）
# 2. Agent 服务化（每个 Agent 作为独立服务）
# 3. 结果收集与汇总
```

#### 4. 状态一致性

```python
# 问题：多个 Agent 实例可能运行在不同进程/机器上
# 解决方案：

# 方案 A：共享状态存储
class DistributedMsgHub(MsgHub):
    def __init__(self, participants, redis_client):
        super().__init__(participants)
        self.redis = redis_client

    async def broadcast(self, msg: Msg):
        # 广播到 Redis Pub/Sub
        self.redis.publish("msghub", msg.json())

# 方案 B：Agent 服务化
# 每个 Agent 暴露 HTTP/gRPC 接口
# Pipeline 通过 HTTP 调用执行远程 Agent
```

#### 5. 实际分布式部署架构

```
┌──────────────────────────────────────────────────────────────┐
│                        API Gateway                            │
│                  （任务分发，结果聚合）                        │
└─────────────────────────┬────────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Agent S1    │  │  Agent S2    │  │  Agent S3    │
│  (服务实例)   │  │  (服务实例)   │  │  (服务实例)   │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
              ┌──────────────────────┐
              │    Message Queue    │
              │  (Redis/RabbitMQ)   │
              └──────────────────────┘
```

#### 6. 开发建议

1. **本地开发调试**：使用本地 MsgHub + Pipeline，确保逻辑正确
2. **单元测试**：Mock 远程调用，本地验证协作逻辑
3. **集成测试**：使用 Docker Compose 启动多个 Agent 实例
4. **生产部署**：Kubernetes + Service Mesh（Istio）管理 Agent 服务

---

### 5.10.6 最佳实践

1. **合理使用 MsgHub**：不需要互相通知时不要使用 MsgHub，增加复杂度
2. **控制参与者和轮次**：过多 Agent 在一个 MsgHub 中会导致消息风暴
3. **使用结构化输出**：在需要确定性结果的场景（如辩论评判），使用 `structured_model`
4. **Pipeline 组合**：复杂流程拆分为多个简单 Pipeline 嵌套
5. **异常处理**：Pipeline 执行中某个 Agent 失败时，需要考虑是否继续

```python
# 推荐：明确指定参与者
async with MsgHub(participants=[alice, bob]) as hub:
    await alice(topic)

# 不推荐：全局广播（难以追踪）
async with MsgHub(participants=get_all_agents()) as hub:
    ...
```

## 5.11 Voice Agent (语音代理)

AgentScope 支持语音交互，是 2.0 时代的核心方向：

### 语音输出配置

```python
from agentscope.agent import ReActAgent
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.tool import Toolkit

# 创建带语音输出的 Agent
voice_agent = ReActAgent(
    name="语音助手",
    model=DashScopeChatModel(model_name="qwen-audio", api_key="your-api-key"),
    sys_prompt="你是一个语音助手，用简洁自然的方式回答问题。",
    formatter=DashScopeChatFormatter(),
    toolkit=Toolkit(),
)

# 对话时自动生成语音
response = await voice_agent(Msg("user", "请介绍一下你自己", "user"))
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

## 5.12 Agent 类型一览

| Agent 类型 | 说明 | 引入版本 |
|-----------|------|----------|
| `ReActAgent` | 核心推理 Agent | v1.0 |
| `UserAgent` | 用户交互 Agent | v1.0 |
| `DeepResearchAgent` | 深度研究 Agent（examples/ 中的示例，非核心 API） | v1.0.19 |
| `RealtimeAgent` | 实时语音 Agent | v2.0 (规划中) |
| `A2AAgent` | Agent-to-Agent 通信 | v1.0 |

## 5.13 下一步

### 推荐阅读

- [AgentScope 官方文档](https://agentscope.readthedocs.io/)
- [示例代码库](https://github.com/modelscope/agentscope/tree/main/examples)

### 扩展学习

1. **深入研究 Pipeline 源码**：阅读 `src/agentscope/pipeline/` 目录下的实现
2. **实践多 Agent 协作**：参考 `examples/workflows/` 下的示例
3. **了解分布式部署**：研究 Agent 服务化与消息队列集成

### 架构设计要点总结

```
┌─────────────────────────────────────────────────────────────┐
│                      架构设计核心要点                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. 分层模块化                                              │
│     应用层 → 服务层 → 基础设施层                             │
│                                                              │
│  2. 元类 Hook 机制                                          │
│     pre_reply, post_reply, pre_observe, post_observe        │
│                                                              │
│  3. 多智能体协作                                            │
│     MsgHub (订阅者广播) + Pipeline (顺序/并行)                │
│                                                              │
│  4. 扩展性设计                                              │
│     继承 ChatModelBase 实现自定义 Model, Agent, Tool         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 本章小结

本章深入剖析了 AgentScope 的架构设计，核心要点包括：

1. **分层架构**：应用层（examples/）→ 服务层（src/ 各模块）→ 基础设施层（HTTP/asyncio/Redis），层次清晰、职责分明
2. **Agent 继承体系**：AgentBase（Hook + 广播）→ ReActAgentBase（推理 + 行动抽象）→ ReActAgent（完整实现），通过元类 `_AgentMeta` 和 `_ReActAgentMeta` 实现 AOP 风格的 Hook 拦截
3. **核心调用链**：`__call__` → `reply` → `_reasoning`（格式化 + 模型调用）→ `_acting`（工具执行）循环，直至无工具调用时返回
4. **多 Agent 协作**：MsgHub 管理订阅者广播，Pipeline 支持 sequential（顺序）和 fanout（并行）两种模式，两者可灵活组合
5. **Agent 类型**：ReActAgent（通用推理）、UserAgent（用户输入）、RealtimeAgent（实时语音）、A2AAgent（Agent 间通信），覆盖不同交互场景

**下一步建议**：结合 `src/agentscope/` 源码，按 5.14.9 的阅读建议顺序深入理解各模块实现细节。

## 练习题

### 练习 5.1: 分层架构理解 [基础]

**题目**：
请根据 AgentScope 的分层架构，将以下模块放入正确的层级：

**模块列表**：
- `src/agentscope/agent/_react_agent.py`
- `src/agentscope/model/_openai_model.py`
- `requests` 库
- `examples/agent/react_agent/main.py`
- `src/agentscope/pipeline/_msghub.py`
- `redis` 库

**验证方式**：
检查分类是否正确。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

```
┌─────────────────────────────────────────────────────────────┐
│                     分层架构                                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  应用层 (Application Layer)                                  │
│  ├── examples/agent/react_agent/main.py                     │
│                                                             │
│  ─────────────────────────────────────────────────────────  │
│                                                             │
│  服务层 (Service Layer)                                      │
│  ├── src/agentscope/agent/_react_agent.py                  │
│  ├── src/agentscope/model/_openai_model.py                 │
│  ├── src/agentscope/pipeline/_msghub.py                    │
│                                                             │
│  ─────────────────────────────────────────────────────────  │
│                                                             │
│  基础设施层 (Infrastructure Layer)                           │
│  ├── requests 库 (HTTP Client)                             │
│  ├── redis 库 (Redis 客户端)                                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**层级职责**：
| 层级 | 职责 | 特点 |
|------|------|------|
| 应用层 | 示例代码、使用案例 | 面向终端用户 |
| 服务层 | 核心业务逻辑、Agent/Model/Pipeline 等 | 框架核心 |
| 基础设施层 | HTTP、数据库、网络通信 | 可替换的外部依赖 |
</details>

---

### 练习 5.2: Agent 继承体系 [中级]

**题目**：
以下是 AgentScope 的 Agent 继承体系（简化版），请在括号中填入正确的类名：

```
StateModule
      │
      ▼
  AgentBase  ──使用──>  (_AgentMeta) 元类
      │
      ▼
ReActAgentBase  ──使用──>  (_ReActAgentMeta) 元类
      │
      ▼
    (    )  ← 这是最常用的推理 Agent
      │
      │
      └───────────> RealtimeAgent
```

**验证方式**：
对照文档中的继承体系图进行检查。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

```
StateModule
      │
      ▼
  AgentBase  ──使用──>  _AgentMeta 元类
      │
      ▼
ReActAgentBase  ──使用──>  _ReActAgentMeta 元类
      │
      ▼
   ReActAgent   ← 这是最常用的推理 Agent
      │
      │
      └───────────> RealtimeAgent
```

**关键说明**：
1. **`_AgentMeta`**：包装 `reply`、`print`、`observe` 方法为 Hook 形式
2. **`_ReActAgentMeta`**：继承自 `_AgentMeta`，额外包装 `_reasoning` 和 `_acting` 方法
3. **`ReActAgent`**：继承 `ReActAgentBase`，实现完整的 ReAct 推理循环
4. **`RealtimeAgent`**：独立实现，不继承自 `ReActAgentBase`，专为实时语音设计

**元类机制的作用**：
- 在类创建时自动为指定方法添加 Hook 包装器
- 实现 AOP（面向切面编程）风格的拦截
- 可以在方法执行前后插入日志、监控等逻辑
</details>

---

### 练习 5.3: ReActAgent 调用链分析 [中级]

**题目**：
小张想了解从用户输入到 Agent 回复的完整调用链。请按顺序排列以下步骤：

A. `toolkit.call_tool_function()` 执行工具
B. `memory.add()` 保存消息到记忆
C. `formatter.format()` 格式化消息
D. `model()` 调用 LLM API
E. `agent()` 或 `agent.reply()` 入口
F. 检查 `tool_use` blocks 判断是否需要工具调用

**验证方式**：
检查步骤顺序是否正确。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**完整调用链（按顺序）**：

```
1.  E. agent() 或 agent.reply() 入口
         │
         ▼
2.  B. memory.add() 保存消息到记忆
         │
         ▼
3.  C. formatter.format() 格式化消息
         │
         ▼
4.  D. model() 调用 LLM API
         │
         ▼
5.  F. 检查 tool_use blocks 判断是否需要工具调用
         │
         ├─── 如果没有工具调用 ───▶ 返回结果给用户
         │
         └─── 如果有工具调用 ───┐
                                ▼
                         A. toolkit.call_tool_function() 执行工具
                                │
                                ▼
                         返回工具结果，进入下一轮推理循环
```

**ReAct 循环内的调用顺序**：
```
_reasoning() 阶段：
  memory.get_memory() → formatter.format() → model() → 解析响应

_acting() 阶段：
  检查 tool_use blocks → toolkit.call_tool_function() → 将结果加入记忆

循环直到：无 tool_use blocks → 返回最终回复
```

**简化版调用链**：
```
用户输入 → add to memory → reasoning（LLM调用）→ acting（工具执行）
                                              ↑                   │
                                               └── loop back ─────┘
 直到完成 → 返回结果
```
</details>

---

### 练习 5.4: MsgHub vs Pipeline [挑战]

**题目**：
某团队需要构建一个"AI 辩论系统"，需求如下：
1. 两名辩手（正方、反方）需要互相看到对方的论点
2. 每轮辩论后，主持人需要点评
3. 辩论最多进行 3 轮

请设计这个系统，说明应该使用 MsgHub、Pipeline 还是两者的组合，并给出代码结构。

**验证方式**：
检查设计方案是否合理，是否正确使用 MsgHub 和 Pipeline。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**设计方案：MsgHub + Pipeline 组合**

**原因**：
- **MsgHub**：让辩手互相看到对方的论点（广播机制）
- **Pipeline**：实现主持人的点评环节（顺序执行）

**代码结构**：

```python
from agentscope.agent import ReActAgent
from agentscope.pipeline import MsgHub, SequentialPipeline
from agentscope.message import Msg

# 创建辩手
pro_agent = ReActAgent(name="正方", ...)
con_agent = ReActAgent(name="反方", ...)
judge = ReActAgent(name="主持人", ...)

# 辩论循环
for round_num in range(3):
    # 使用 MsgHub 实现互相听到对方
    async with MsgHub(participants=[pro_agent, con_agent]) as hub:
        # 正方发言
        pro_statement = await pro_agent(
            Msg("user", f"第 {round_num + 1} 轮正方论点：{topic}", "user")
        )
        
        # 反方看到正方论点后回应
        con_statement = await con_agent(
            Msg("user", f"第 {round_num + 1} 轮反方论点：{topic}", "user")
        )

    # 主持人点评（MsgHub 外独立执行）
    judge_comment = await judge(
        Msg("user", f"点评第 {round_num + 1} 轮辩论", "user")
    )

# 最终判断（使用 Pipeline）
final_judge = SequentialPipeline(agents=[judge])
result = await final_judge("请给出最终裁决")
```

**关键设计点**：
1. **MsgHub 内**：正方和反方在同一上下文，互相能收到对方的发言
2. **MsgHub 外**：主持人独立点评，不影响辩手之间的对话
3. **循环控制**：通过 `for` 循环限制辩论轮数

**替代方案对比**：

| 方案 | 适用场景 |
|------|----------|
| 仅 MsgHub | 多人自由讨论，无固定流程 |
| 仅 Pipeline | 固定流程，无互相听到的需求 |
| MsgHub + Pipeline | 需要分组讨论 + 全局汇总的场景 |
</details>

---

### 练习 5.5: 源码文件定位 [基础]

**题目**：
小刘想阅读 AgentScope 的源码来理解 MsgHub 的实现。请回答：

1. MsgHub 的源码在哪个目录下？
2. 文件名是什么？
3. MsgHub 类的核心方法有哪些？

**验证方式**：
对照文档中的源码文件位置索引。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**1. MsgHub 源码位置**：
```
src/agentscope/pipeline/_msghub.py
```

**2. 目录结构**：
```
src/agentscope/
├── pipeline/               # Pipeline 模块
│   ├── _msghub.py         # MsgHub 消息中心
│   ├── _functional.py     # 函数式管道
│   ├── _class.py          # Pipeline 类封装
│   └── __init__.py
```

**3. MsgHub 核心方法**：

| 方法 | 作用 |
|------|------|
| `__init__()` | 初始化参与者列表和广播配置 |
| `__aenter__()` | 进入上下文，重置订阅者，广播公告消息 |
| `__aexit__()` | 退出上下文，清理订阅者 |
| `broadcast()` | 手动广播消息给所有参与者 |
| `add()` | 添加参与者 |
| `delete()` | 删除参与者 |
| `set_auto_broadcast()` | 开启/关闭自动广播 |

**源码阅读建议**：
- 阅读顺序：`__init__` → `broadcast` → `__aenter__` / `__aexit__`
- 重点理解订阅者机制和广播逻辑
</details>

## 5.13 下一步
