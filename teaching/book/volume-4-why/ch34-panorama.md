# 第三十四章 架构的全景与边界

这是本书的最后一章。前七章分别审视了单个设计决策——为什么用 `Msg` 而不是字典，为什么工具不用装饰器，为什么 `ReActAgent` 是千行大类，为什么钩子挂在类上，为什么类型用 `dict` 和 `Union`，为什么用 `ContextVar` 管理状态，为什么 `Formatter` 和 `Model` 分离。现在退后一步，看整片森林。

## 一、决策回顾

### 1.1 模块地图

`src/agentscope/` 下有 25 个子目录。`__init__.py`（第 43-59 行）按固定顺序导入它们：

```
exception          # 自定义异常
module             # StateModul e —— 所有有状态对象的基类
message            # Msg、TextBlock、ToolUseBlock 等消息类型
model              # ChatModelBase 及 7 个提供商适配器
tool               # Toolkit、ToolResponse、内置工具函数
formatter          # FormatterBase 及 7 个格式化实现
memory             # 工作记忆 + 长期记忆（6 种实现）
agent              # AgentBase -> ReActAgent -> A2AAgent / RealtimeAgent
session            # 会话持久化（JSON / Redis / Tablestore）
embedding          # 向量嵌入（4 个提供商）
token              # Token 计数
evaluate           # 评估与基准测试
pipeline           # 流水线编排（Sequential / Fanout / MsgHub / ChatRoom）
tracing            # OpenTelemetry 追踪
rag                # RAG：文档读取 + 向量存储 + 知识库
a2a                # Agent-to-Agent 协议
realtime           # 实时语音交互
mcp                # Model Context Protocol 客户端
tts                # 文本转语音（5 个实现）
tuner              # 模型微调与 RL
plan               # 任务规划（PlanNotebook）
types              # 类型别名（ToolFunction、Embedding 等）
hooks              # Studio 钩子
_utils             # 内部工具函数
```

这不是平铺排列。模块之间存在明确的依赖方向。

### 1.2 依赖图

通过分析各模块 `__init__.py` 和核心文件的 `from ..` 导入，可以画出依赖层次：

```
第一层（无外部依赖）：
    exception, types, _utils, _logging

第二层（依赖第一层）：
    module (依赖 _logging)
    message (无跨模块依赖)
    token (无跨模块依赖)

第三层（依赖第一、二层）：
    model (依赖 _model_response，无其他模块依赖)
    formatter (依赖 message, _utils/_common)
    embedding (无跨模块依赖)
    session (无跨模块依赖)

第四层（依赖下层模块）：
    tool (依赖 message, module, types, _utils, mcp)
    memory (依赖 message, module)
    tts (无跨模块依赖)
    plan (无跨模块依赖)
    tracing (依赖 message, model, embedding, _logging)

第五层（核心编排层）：
    agent (依赖 module, message, types, model, formatter, memory,
           tool, plan, token, rag, tts, tracing)

第六层（应用层）：
    pipeline (依赖 agent, message)
    rag (依赖 embedding, message, tool)
    a2a (独立模块)
    realtime (独立模块)
```

依赖图呈菱形结构。底部是基础设施（`types`、`message`、`_logging`），中间是能力层（`model`、`formatter`、`tool`、`memory`），顶部是编排层（`agent`、`pipeline`）。依赖方向几乎完全自底向上——上层模块导入下层，下层不知道上层的存在。

`agent` 是最大的依赖汇合点。`ReActAgent`（`_react_agent.py`，共 1137 行）同时导入了 `model`、`formatter`、`memory`、`tool`、`plan`、`token`、`rag`、`tts`、`tracing` 九个模块。它是一个真正的编排中心——也是最重的耦合点。

### 1.3 初始化顺序

`agentscope.init()`（`__init__.py`，第 72-156 行）揭示了框架的启动逻辑：

```python
# 第 115 行：先设置日志
setup_logger(logging_level, logging_path)

# 第 117-145 行：如果配置了 studio_url，修改 UserAgent 的输入方式
if studio_url:
    UserAgent.override_class_input_method(StudioUserInput(...))
    _equip_as_studio_hooks(studio_url)

# 第 147-156 行：最后设置追踪
if endpoint:
    from .tracing import setup_tracing
    setup_tracing(endpoint=endpoint)
```

初始化不创建 Agent、Model 或 Memory。它只配置全局状态（日志、Studio 连接、追踪端点）。用户在 `init()` 之后自行构造所需对象。这是"框架不持有你的对象"设计哲学的体现。

## 二、被否方案

### 方案 A：单体架构

把所有功能放在一个包里，不做模块拆分：

```
agentscope/
    agent.py        # Agent + Memory + Tool + Model 全在一起
    utils.py        # 所有工具函数
```

这在项目早期（少于 5 个模块时）是合理的。但当提供商从 1 个扩展到 7 个，记忆从 1 种扩展到 6 种，单体文件会膨胀到无法维护。AgentScope 的 `tool/_toolkit.py` 已经有 1684 行，如果把 Formatter 和 Model 的逻辑也塞进来，单个文件可能超过 5000 行。

单体架构的优势是简单——没有跨模块导入，没有 `__init__.py` 的导出管理。但这个优势在代码量超过一万行后迅速消失。

### 方案 B：微服务架构

把每个模块拆成独立服务，通过 HTTP/gRPC 通信：

```
model-service/      # 独立进程，提供模型调用 API
memory-service/     # 独立进程，提供记忆存储 API
tool-service/       # 独立进程，提供工具执行 API
agent-service/      # 编排层，调用其他服务
```

问题在于调用频率。一次 `ReActAgent` 推理循环可能涉及 3-5 次模型调用、5-10 次记忆读写、2-3 次工具格式化。如果每次都是网络请求，延迟会从毫秒级膨胀到百毫秒级。在流式输出场景中，这种延迟完全不可接受。

此外，微服务引入了序列化成本。`Msg` 对象在服务间传递需要 JSON 序列化/反序列化，而 `formatter.format()` 本质上就是在做这件事。如果每次调用都序列化，Formatter 的分离就失去了性能优势。

### 方案 C：插件架构

把每个模块定义为可插拔的插件，通过注册中心动态加载：

```python
class PluginRegistry:
    def register_model(self, name: str, cls: Type[ChatModelBase]): ...
    def register_formatter(self, name: str, cls: Type[FormatterBase]): ...
    def register_memory(self, name: str, cls: Type[MemoryBase]): ...

# 用户代码
registry = PluginRegistry()
registry.register_model("openai", OpenAIChatModel)
registry.register_model("anthropic", AnthropicChatModel)
```

AgentScope 没有采用这种架构。模块在 `__init__.py` 中静态导入（第 43-59 行），不是动态注册。原因是：

1. **Python 的模块系统已经是插件系统**。`from .model import OpenAIChatModel` 和 `registry.register_model("openai", OpenAIChatModel)` 在功能上等价，但前者不需要额外的抽象层。
2. **静态导入提供更好的 IDE 支持**。跳转到定义、自动补全、类型检查都在静态导入下开箱即用。
3. **注册中心增加间接层**。用户需要理解注册、查找、生命周期管理，而直接导入不需要这些概念。

LangChain 一定程度上采用了插件模式——它的 `ChatModel` 通过 `class_name` 字符串查找实现类。这提供了灵活性（用户可以通过字符串指定模型），但也带来了调试困难（导入错误在运行时才暴露）。

## 三、后果分析

### 3.1 清晰的边界

AgentScope 中最干净的模块边界是 `model` 和 `formatter` 之间的分离。如前一章所述，`ChatModelBase` 不认识 `Msg`，`FormatterBase` 不知道 HTTP。两者通过 `list[dict]` 这个中性数据结构通信。

`message` 模块同样边界清晰。`Msg`（`_message_base.py`）和各个 `Block` 类型（`_message_block.py`）不导入框架的任何其他模块。它们是纯数据结构，可以被任何模块安全引用。

`types` 模块（`types/__init__.py`）定义了框架级的类型别名：`ToolFunction`、`Embedding`、`JSONSerializableObject`。它不包含任何运行时逻辑，只包含类型定义。这是依赖图中最稳定的节点——它几乎不会变化。

### 3.2 模糊的边界

`_utils/_common.py`（503 行）是一个边界模糊的例子。它包含了：

- JSON 修复（`_json_loads_with_repair`，第 31-69 行）——服务于模型流式输出的解析
- 工具函数 JSON Schema 提取（`_parse_tool_function`，第 339-455 行）——服务于工具注册
- MCP 工具 Schema 转换（`_extract_json_schema_from_mcp_tool`，第 216-236 行）——服务于 MCP 协议
- 音频重采样（`_resample_pcm_delta`，第 458-502 行）——服务于实时语音
- Base64 编解码（`_save_base64_data`，第 192-213 行）——服务于 Formatter

这些函数唯一的共同点是"不知道放哪里更合适"。它们不构成一个内聚的模块，而是跨领域工具函数的集合。

`_parse_tool_function` 是典型案例。它被 `tool/_toolkit.py` 导入（通过 `from .._utils._common import _parse_tool_function`），用于把 Python 函数的签名和 docstring 转换成 JSON Schema。这个函数依赖 `docstring_parser`、`pydantic`、`inspect` 三个外部库，内部逻辑超过 100 行。它实际上是工具系统的核心组件，但因为历史原因放在了 `_utils` 里。

这引出一个架构问题：当一个工具函数被多个模块使用，但逻辑上属于某个特定模块时，应该放在哪里？AgentScope 的选择是统一放在 `_utils/_common.py`，代价是这个文件变成了"边界模糊区"。

### 3.3 `tool` 模块的职责膨胀

`tool/_toolkit.py`（1684 行）是框架中最大的单文件。它同时处理：

- 工具注册（`register_tool_function`）
- JSON Schema 生成
- 异步/同步工具执行
- MCP 客户端管理
- 工具分组（`ToolGroup`）
- Agent 技能（`AgentSkill`）
- 流式工具调用

文件顶部的 TODO 注释（第 3 行）已经承认了这个问题：

```python
# TODO: We should consider to split this `Toolkit` class in the future.
```

这个 TODO 说明架构师意识到了膨胀，但拆分的优先级不够高。原因可能是：拆分需要仔细处理公共状态（`Toolkit` 内部同时持有注册表、MCP 客户端、工具分组，它们共享 `_registered_tools` 字典），而目前的功能还在快速增长。

### 3.4 `agent` 的依赖扇出

`ReActAgent` 的导入列表（`_react_agent.py`，第 1-30 行）是整个框架依赖最密集的位置：

```python
from ..formatter import FormatterBase
from ..memory import MemoryBase, LongTermMemoryBase, InMemoryMemory
from ..message import Msg, ToolUseBlock, ToolResultBlock, TextBlock, AudioBlock
from ..model import ChatModelBase
from ..rag import KnowledgeBase, Document
from ..plan import PlanNotebook
from ..token import TokenCounterBase
from ..tool import Toolkit, ToolResponse
from ..tracing import trace_reply
from ..tts import TTSModelBase
```

11 个直接导入。`ReActAgent` 知道框架的几乎每一个模块。这意味着：

1. **任何模块的接口变化都可能影响 Agent**。这是依赖扇出的天然后果。
2. **Agent 是测试的瓶颈**。测试 `ReActAgent` 需要 mock 11 个依赖。
3. **新功能倾向于往 Agent 里加**。因为 Agent 已经导入了所有模块，添加新功能不需要新增导入——只需要在 `__init__` 里加一个参数，在 `_reasoning` 里加一个分支。

这种依赖结构是"上帝类"（God Object）的典型特征。前文已经讨论了 `ReActAgent` 作为千行类的决策。这里要补充的是：上帝类的问题不在于代码行数，而在于它成为所有变化的交汇点。

### 3.5 循环依赖的规避

Python 不允许模块间的循环导入。AgentScope 通过以下策略规避循环依赖：

1. **延迟导入**。`__init__.py` 第 135 行在 `init()` 函数内部导入 `UserAgent`：`from .agent import UserAgent, StudioUserInput`。这不是顶层导入，而是按需导入，避免了 `__init__` 和 `agent` 之间的循环。
2. **TYPE_CHECKING 守卫**。`_common.py` 第 25-28 行：
   ```python
   if typing.TYPE_CHECKING:
       from mcp.types import Tool
   else:
       Tool = "mcp.types.Tool"
   ```
   这让类型检查器看到正确的类型，但运行时不触发导入。
3. **`_utils` 作为中间层**。工具函数放在 `_utils/_common.py`，而不是 `tool/_toolkit.py`，部分原因是避免 `formatter` 和 `tool` 之间的直接依赖。

### 3.6 演化方向

从当前结构可以推测几个演化方向：

**`Toolkit` 拆分**。当 TODO 被执行时，`_toolkit.py` 可能拆成 `_toolkit_registry.py`（注册逻辑）、`_toolkit_executor.py`（执行逻辑）、`_toolkit_mcp.py`（MCP 集成）。这是增量重构，不影响外部接口。

**`_utils/_common.py` 收缩**。随着模块边界清晰化，模糊地带的函数应该回归各自归属的模块。`_parse_tool_function` 去 `tool/`，`_save_base64_data` 去 `formatter/`，`_resample_pcm_delta` 去 `realtime/`。`_utils` 最终只保留真正跨领域的基础函数（如 `_get_timestamp`、`_is_async_func`）。

**Agent 接口分层**。`ReActAgent` 的构造函数参数已经超过 20 个（第 177-220 行），其中大部分有默认值。未来可能引入 Builder 模式或配置对象，将构造参数分组：

```python
# 可能的演化方向（推测，非源码）
agent = ReActAgent(
    name="assistant",
    model_config=ModelConfig(model=..., formatter=...),
    memory_config=MemoryConfig(memory=..., long_term=...),
    tool_config=ToolConfig(toolkit=..., mcp_clients=...),
)
```

**协议层扩展**。`a2a` 和 `mcp` 是两个新加入的协议模块。它们不依赖框架的核心模块（`agent`、`model`、`memory`），而是定义了自己的接口。未来可能有更多协议模块加入（如 ACP、AGP），它们会构成框架的"协议层"，与核心层平行。

## 四、横向对比

### LangChain

LangChain 的架构是水平分层的：`langchain-core`（基础抽象）、`langchain-community`（社区集成）、`langchain`（编排逻辑）。每一层都包含多种模块（模型、工具、记忆、链）。

AgentScope 的架构是垂直分层的：每个模块（`model`、`tool`、`memory`）从接口到实现都在同一个包内完成。

水平分层的好处是核心包保持精简。LangChain 的 `langchain-core` 不依赖任何提供商 SDK，社区集成放在 `langchain-community` 中。坏处是跨层依赖复杂——`ChatOpenAI` 在 `community` 包中，但继承自 `core` 包的 `BaseChatModel`，用户需要理解包之间的版本兼容性。

垂直分层的好处是模块自包含。添加一个新的模型提供商只需要修改 `model/` 包，不需要动其他包。坏处是核心包较大——AgentScope 的 `src/agentscope/` 包含了所有功能。

### AutoGen

微软的 AutoGen（0.4 版本）采用了更激进的模块化。核心包 `autogen-core` 只提供 Agent 抽象和消息传递，不包含模型调用、工具执行等具体实现。这些通过扩展包提供。

这比 AgentScope 更符合微内核架构的理念。但代价是复杂度前移——用户需要理解包的组合关系，不能 `import agentscope` 一走了之。

AgentScope 选择了"大包"策略：所有核心功能在一个包内，通过 `pip install agentscope[full]` 安装。优点是入门门槛低，缺点是包体积大。

### CrewAI

CrewAI 是一个更年轻、更聚焦的框架。它的架构比 AgentScope 简单得多：Agent、Task、Crew 三个核心概念，没有独立的 Formatter、Memory 抽象，工具注册用装饰器。

这种简洁在项目初期是优势。但随着功能增长，CrewAI 面临着 AgentScope 已经解决过的问题——工具注册需要更灵活的方式、多提供商支持需要格式化层、多 Agent 编排需要消息传递机制。AgentScope 的架构复杂度不是过度设计，而是功能丰富度的自然结果。

### 架构风格总结

| 框架 | 架构风格 | 模块数量 | 依赖方向 |
|---|---|---|---|
| AgentScope | 分层单体 | 25 个子包 | 自底向上，agent 是汇合点 |
| LangChain | 水平分层 | 3 层，每层多个包 | 跨层继承 |
| AutoGen | 微内核 | 核心包 + 扩展包 | 核心不依赖扩展 |
| CrewAI | 简单单体 | 3 个核心类 | 扁平 |

没有哪种风格绝对优于其他。选择取决于框架的定位：AgentScope 追求功能完整性和一致体验，LangChain 追求生态广度，AutoGen 追求最小核心，CrewAI 追求快速上手。

## 五、你的判断

这是全书最后一组问题。它们不是练习题，而是关于 AgentScope 和多 Agent 框架未来的开放思考。

1. **大包的边界在哪里**。AgentScope 目前包含 25 个子模块，从 RAG 到微调到实时语音。是否有模块应该拆出去成为独立包？判断标准是什么——是依赖方向（如果模块不依赖 `agent`，就可以独立），还是功能领域（RAG 和框架核心是否有不同的发布节奏）？

2. **上帝类的宿命**。`ReActAgent` 是框架的入口，也是最大的耦合点。在框架演进中，上帝类是不可避免的结构（用户需要一个"能做所有事"的 Agent），还是架构缺陷（应该拆成可组合的小 Agent）？如果选择拆分，用户体验会怎样变化？

3. **依赖图会收敛还是发散**。当前 `agent` 依赖 11 个模块，其他模块互不依赖。随着 `mcp`、`a2a`、`realtime` 等新模块成熟，它们是否会开始互相依赖？例如，`realtime` 是否会需要 `tool`？`a2a` 是否会需要 `memory`？如果依赖图从树变成网，分层架构是否还能维持？

4. **框架的规模效应**。AgentScope 支持了 7 个模型提供商、6 种记忆实现、5 种向量存储。每增加一个提供商，就需要一对 Formatter + Model。这种线性增长的维护成本是否有上限？是否有更好的抽象可以降低边际成本？

5. **你的框架**。如果你要写一个多 Agent 框架，你会从 AgentScope 的架构中继承什么，放弃什么？如果你只支持 OpenAI 兼容 API，架构可以简化多少？如果你需要支持浏览器自动化和代码沙箱，架构需要增加什么？

这些问题没有标准答案。架构不是数学证明，不存在"最优解"。每个设计都是在特定约束下的权衡——代码量、团队能力、用户群体、演化速度。理解这些权衡，比记住某个具体的模式更重要。

---

全书完。从 `Msg` 的一行定义到整个框架的依赖图，我们追踪了 AgentScope 中每一个重要的设计决策——做了什么，放弃了什么，承受了什么后果。这些决策叠加在一起，构成了你眼前这个 25 模块、25 万行代码的多 Agent 框架。

下一行代码由你写。
