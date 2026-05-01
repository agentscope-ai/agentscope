# AgentScope 最新信息调研报告

**调研日期**: 2026-04-27
**调研版本**: 1.0.19 及最新动态

---

## 1. 最新版本特性 (v1.0.19)

### 1.1 版本亮点

根据 GitHub Release 信息，v1.0.19 包含以下重要更新：

| 类别 | 变更 | 贡献者 |
|------|------|--------|
| **Formatter** | 支持 `file://` 开头的本地多媒体路径 | @qbc2016 (#1385) |
| **Formatter** | Anthropic formatter 本地文件转 base64 | @qbc2016 (#1361) |
| **Formatter** | DashScope formatter 本地文件转 base64 | @qbc2016 (#1253) |
| **Agent** | DeepResearchAgent memory.add 缺少 await 修复 | @Ricardo-M-L (#1477) |
| **Agent** | DeepResearchAgent 支持 thinking blocks | @Luohh5 (#1492) |
| **Agent** | 防止子类覆盖 `_reasoning`/`_acting` 时重复执行 hook | @qbc2016 (#1481) |
| **Token** | 处理 tool 参数中缺失 'type' 字段 | @octo-patch (#1403) |
| **Memory** | SQLAlchemy memory flush session 确保消息写入 | @qbc2016 (#1396) |
| **UnitTest** | 热修复测试用例 | @qbc2016 (#1507) |

### 1.2 DeepResearchAgent 重要修复

v1.0.19 对 DeepResearchAgent 进行了多项修复，使其更稳定：

```python
# DeepResearchAgent 现在支持 thinking 和 non-thinking 模型
# 修复了 thinking blocks 的处理逻辑
# 注意：DeepResearchAgent 是一个高级模式示例（examples/），非核心库类
from agentscope.agent import DeepResearchAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit

toolkit = Toolkit()
toolkit.register_tool_function(tavily_search)
toolkit.register_tool_function(tavily_extract)

agent = DeepResearchAgent(
    name="研究助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个研究助手。",
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,  # 使用 toolkit 替代 tools
)
```

### 1.3 Formatter 本地文件支持

这是一个重要更新，现在可以直接使用本地文件：

```python
# Anthropic formatter 支持本地图片
from agentscope.formatter import AnthropicChatFormatter

formatter = AnthropicChatFormatter()
# 现在支持 file://path/to/image.jpg 格式
```

---

## 2. AgentScope 2.0 路线图

### 2.1 重要公告

**2026年4月**: AgentScope 2.0 正在开发中！

官方路线图显示 2.0 将带来重大架构升级，但 v1.0 仍持续维护。

### 2.2 Voice Agent 成为核心方向

AgentScope 正在全力投入 Voice Agent 开发，分为三个阶段：

```
Phase 1: TTS (Text-to-Speech) Models
    └── 构建 TTS 模型基类基础设施

Phase 2: Multimodal Models (Non-Realtime)
    └── ReAct agents 多模态支持

Phase 3: Real-time Multimodal Models
    └── 实时语音交互
```

### 2.3 实时语音特性 (2026年2月已实现)

- `ReActAgent` 通过 `speech` 字段支持语音输出
- TTS 集成：DashScope、OpenAI、德国 TTS
- 支持实时/非实时、流式/非流式模式
- WebSocket 长连接低延迟回调

---

## 3. 核心 API 变化

### 3.1 新增 API

| API | 说明 | 文档位置 |
|-----|------|----------|
| `Msg.generate_reason` | 新的推理生成接口 | 核心概念 |
| `Toolkit` 注册接口 | 支持外部工具动态集成 | Tool 章节 |
| `Agent.skip_reasoning` | ReActAgent 可跳过推理阶段 | Agent 章节 |
| `AgentSkill` | 代理技能系统 | 新增章节 |

### 3.2 多智能体模式更新

官方新增多种多智能体协作模式：

```python
from agentscope.agent import ReActAgent
from agentscope.pipeline import SequentialPipeline, FanoutPipeline, MsgHub

# 1. SequentialPipeline - 顺序执行
seq = SequentialPipeline(agents=[researcher, writer])
result = seq("写一篇研究报告")

# 2. FanoutPipeline - 并行广播
fanout = FanoutPipeline(agents=[agent_a, agent_b, agent_c])
results = fanout("广播这个消息")

# 3. MsgHub - 消息中心
async with MsgHub(participants=[agent_a, agent_b, agent_c]) as hub:
    # 消息会自动广播给所有参与者
    pass
```

### 3.3 Agent 类型扩展

```
Agent 类型 (更新后)
├── ReActAgent (核心推理 Agent)
├── UserAgent (用户代理)
├── A2AAgent (Agent-to-Agent 通信) [新增]
├── RealtimeAgent (实时语音 Agent) [新增]
└── DeepResearchAgent (深度研究 Agent，示例模式) [新增]
```

---

## 4. 文档结构更新

### 4.1 官方文档新结构 (doc.agentscope.io)

```
Tutorial
├── Installation
├── Key Concepts
├── Create Message
└── Create ReAct Agent

Workflow (工作流)
├── Conversation
├── Multi-Agent Debate [新增]
├── Concurrent Agents [新增]
├── Routing [新增]
└── Handoffs [新增]

Model and Context
├── Model
├── Prompt Formatter
├── Token
├── Memory
└── Long-Term Memory [重大更新]

Tool
├── Tool
├── MCP [新增]
└── Agent Skill [新增]

Agent
├── Agent
├── State/Session Management
├── Agent Hooks
├── Middleware [新增]
├── A2A Agent [新增]
└── Realtime Agent [新增]

Features
├── Pipeline
├── Plan
├── RAG
├── AgentScope Studio
├── Tracing
├── Evaluation
├── Evaluation with OpenJudge [新增]
├── Embedding [新增]
├── TTS [新增]
└── Tuner [新增]
```

### 4.2 新增功能说明

**AgentScope Studio**: 监控和调试平台
- 双视图消息显示：时间线视图和聚合回复视图
- ReAct agent 中间状态可视化
- OpenTelemetry 全链路追踪集成

**Tuner**: 微调模块
- 零代码修改的微调方案
- 本地调试无需 GPU
- 多智能体微调支持（如 30B 模型）

---

## 5. 最佳实践更新

### 5.1 多智能体设计模式

根据最新文档，推荐以下多智能体模式：

```python
# 模式一：FanoutPipeline - 并行广播 (适用于需要多角度分析)
fanout = FanoutPipeline(agents=[pro_agent, con_agent])
analysis = fanout("分析这个商业决策的利弊")

# 模式二：SequentialPipeline - 顺序执行 (适用于需要前置任务结果)
seq = SequentialPipeline(agents=[researcher, writer])
research = researcher("研究 AI 趋势")
article = writer(f"基于研究写文章: {research}")

# 模式三：MsgHub - 消息中心 (适用于多 Agent 自由对话)
async with MsgHub(participants=[agent1, agent2, agent3]) as hub:
    # 消息自动广播
    pass
```

### 5.2 工具调用最佳实践

```python
from agentscope.tool import Toolkit

# 推荐：为每个工具提供清晰的文档字符串，然后注册到 Toolkit
def search_knowledge_base(query: str, top_k: int = 5) -> str:
    """在知识库中搜索相关内容

    Args:
        query: 搜索查询关键词
        top_k: 返回的最相关结果数量，默认5条

    Returns:
        包含相关文档片段的 JSON 字符串
    """
    ...

# 推荐：使用类型提示
def calculate_metrics(data: list[float], metric: str) -> dict[str, float]:
    """计算数据指标

    Args:
        data: 数值列表
        metric: 指标类型 ("mean", "sum", "std")

    Returns:
        计算结果的字典
    """
    ...

# 使用 Toolkit.register_tool_function() 注册工具
toolkit = Toolkit()
toolkit.register_tool_function(search_knowledge_base)
toolkit.register_tool_function(calculate_metrics)
```

### 5.3 记忆系统最佳实践

```python
# 开发环境：使用 InMemoryMemory
from agentscope.memory import InMemoryMemory
memory = InMemoryMemory()

# 生产环境：使用 RedisMemory
from agentscope.memory import RedisMemory
memory = RedisMemory(
    host="redis.example.com",
    port=6379,
    key_prefix="myapp:agent:"
)

# 需要长期记忆：使用 Mem0
from agentscope.memory import Mem0Memory
memory = Mem0Memory(api_key="...")

# 组合使用短期+长期记忆
from agentscope.memory import HybridMemory
memory = HybridMemory(
    short_term=InMemoryMemory(),
    long_term=Mem0Memory(api_key="...")
)
```

### 5.4 Voice Agent 开发实践

```python
# 创建带语音输出的 Agent
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.tool import Toolkit

agent = ReActAgent(
    name="语音助手",
    model=DashScopeChatModel(model_name="qwen-audio"),
    sys_prompt="你是一个语音助手。",
    formatter=DashScopeChatFormatter(),
    toolkit=Toolkit(),
    speech={  # 新增语音配置
        "tts_api": "dashscope",  # 或 "openai", "german_tts"
        "voice": "female_2",
        "stream": True  # 流式输出
    }
)
```

---

## 6. 示例代码更新建议

### 6.1 推荐更新 quickstart.py

```python
# 旧版 (需要更新)
import agentscope
from agentscope import agent
from agentscope.model import OpenAIChatGPTModel

agentscope.init(project="my-first-agent")

my_agent = agent.ReActAgent(
    name="助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    tools=[agentscope.tool.python_executor]  # 旧 API
)

# 新版
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit, python_executor

agentscope.init(project="my-first-agent")

toolkit = Toolkit()
toolkit.register_tool_function(python_executor)

my_agent = ReActAgent(
    name="助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个有帮助的助手。",
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,  # 使用 toolkit 替代 tools
)
```

### 6.2 多智能体示例更新

```python
# 新增多智能体协作示例
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit
from agentscope.pipeline import SequentialPipeline, FanoutPipeline

# 创建专家 Agent
toolkit = Toolkit()
toolkit.register_tool_function(tavily_search)

researcher = ReActAgent(
    name="研究员",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个研究员。",
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,
)

writer = ReActAgent(
    name="作家",
    model=OpenAIChatModel(model_name="gpt-4o-mini"),
    sys_prompt="你是一个作家。",
    formatter=OpenAIChatFormatter(),
    toolkit=Toolkit(),
)

# 方式一：SequentialPipeline 顺序执行
seq = SequentialPipeline(agents=[researcher, writer])
research_result = researcher("研究 AI Agent 的最新发展趋势")
article = writer(f"根据以下研究写一篇文章: {research_result}")

# 方式二：FanoutPipeline 并行执行
fanout = FanoutPipeline(agents=[researcher, writer, coder])
results = fanout("并行处理这个任务")
```

### 6.3 DeepResearchAgent 使用

```python
# 新增：深度研究 Agent
# 注意：DeepResearchAgent 是一个高级模式示例（位于 examples/），非核心库类
from agentscope.agent import DeepResearchAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit

toolkit = Toolkit()
toolkit.register_tool_function(tavily_search)   # Web 搜索
toolkit.register_tool_function(tavily_extract)  # 内容提取
toolkit.register_tool_function(text_file)       # 文件读取

research_agent = DeepResearchAgent(
    name="深度研究助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个深度研究助手。",
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,
    max_depth=3,  # 研究深度
    max_tokens=100000  # 研究报告最大长度
)

report = research_agent("研究 AI 在医疗领域的应用现状和未来趋势")
```

---

## 7. 重要注意事项

### 7.1 版本兼容性

- AgentScope 2.0 正在开发中，但 v1.0 仍为主力版本
- 新项目建议使用 v1.0.19+
- 2.0 将有 breaking changes，提前关注官方公告

### 7.2 向后兼容性

- `agentscope.tool.python_executor` API 变更：现在需要显式导入
- `DeepResearchAgent` 现在支持 thinking 和 non-thinking 模型
- Tavily 工具函数命名已从驼峰改为下划线

### 7.3 弃用提示

| 旧 API | 新 API | 备注 |
|--------|--------|------|
| `agentscope.tool.xxx` | `from agentscope.tool import xxx` | 模块导入方式变更 |
| 驼峰命名的工具 | 下划线命名 | 如 `tavilySearch` → `tavily_search` |

---

## 8. 参考资源

- **官方文档**: https://doc.agentscope.io/
- **GitHub 仓库**: https://github.com/agentscope-ai/agentscope
- **更新日志**: https://github.com/agentscope-ai/agentscope/blob/main/docs/NEWS.md
- **路线图**: https://github.com/agentscope-ai/agentscope/blob/main/docs/roadmap.md
- **AgentScope Java**: https://java.agentscope.io/ (Java 开发者专用)
- **Discord 社区**: https://discord.gg/eYMpfnkG8h

---

## 9. 下一步建议

1. **更新教程文档**: 根据本文档更新 quickstart 和核心概念章节
2. **补充示例代码**: 添加 DeepResearchAgent、SequentialPipeline、FanoutPipeline 等多智能体协作示例
3. **更新 API 引用**: 修正模块导入方式的示例代码
4. **添加 Voice Agent 章节**: 考虑在高级特性中添加语音交互内容
5. **关注 2.0 动态**: 跟踪 AgentScope 2.0 开发进展，及时更新文档

---

*报告生成时间: 2026-04-27*
*调研版本: AgentScope v1.0.19*
