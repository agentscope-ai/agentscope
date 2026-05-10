# 第 13 章：模块系统——文件的命名与导入

> **难度**：入门
>
> 你 clone 了仓库，打开 `src/agentscope/` 看到一堆 `_` 开头的文件——`_agent_base.py`、`_react_agent.py`、`_model_base.py`……为什么有些文件有下划线前缀？导入路径又是怎么组织的？

## 知识补全：Python 模块与包

Python 用文件和目录组织代码：

- 一个 `.py` 文件就是一个**模块**（module）
- 一个包含 `__init__.py` 的目录就是一个**包**（package）
- `from agentscope.agent import ReActAgent` 的意思是：从 `agentscope` 包的 `agent` 子包中导入 `ReActAgent`

`__init__.py` 决定了 `from package import X` 时能导入什么。

---

## 命名规则：下划线前缀

打开 `src/agentscope/` 的任意子目录：

```
src/agentscope/agent/
├── __init__.py
├── _agent_base.py           # 下划线前缀 = 内部实现
├── _react_agent_base.py
├── _react_agent.py
├── _user_agent.py
├── _user_input.py
├── _a2a_agent.py
└── _realtime_agent.py
```

**规则**：以 `_` 开头的文件是**内部实现**，不应被外部直接导入。公共 API 通过 `__init__.py` 暴露。

### 为什么这样做？

```python
# 不推荐：直接导入内部文件
from agentscope.agent._react_agent import ReActAgent

# 推荐：通过包的公共 API
from agentscope.agent import ReActAgent
```

`__init__.py` 充当"门面"，控制哪些类/函数对外可见：

```python
# agent/__init__.py:3-6
from ._agent_base import AgentBase
from ._react_agent_base import ReActAgentBase
from ._react_agent import ReActAgent
```

这意味着：
1. **内部文件可以自由重构**——只要 `__init__.py` 的导出不变，外部代码不受影响
2. **IDE 自动补全更干净**——只显示公共 API，不显示内部实现
3. **文档更有条理**——公共 API 就是 `__init__.py` 中列出的那些

> **设计一瞥**：下划线前缀是 Python 社区的惯例，不是语言强制的。
> Python 没有真正的"私有"概念。`_` 前缀只是一种约定，告诉其他开发者"这是内部实现，请勿直接使用"。
> 但在框架设计中，这个约定非常重要——它定义了"公共 API 边界"，让框架作者可以在内部自由修改而不破坏用户代码。

---

## 导入路径的三层结构

AgentScope 的导入路径是这样的：

```
agentscope                    # 顶层包
├── .agent                    # 子包
│   ├── ReActAgent            # 公共类
│   ├── UserAgent
│   └── AgentBase
├── .model                    # 子包
│   ├── OpenAIChatModel
│   ├── AnthropicChatModel
│   └── ChatModelBase
├── .formatter
│   └── OpenAIChatFormatter
├── .tool
│   ├── Toolkit
│   └── ToolResponse
├── .memory
│   ├── InMemoryMemory
│   └── MemoryBase
├── .message
│   ├── Msg
│   └── TextBlock, ToolUseBlock, ...
└── .module
    └── StateModule
```

### 顶层 `__init__.py`

`src/agentscope/__init__.py` 做了两件事：

**1. 导入子模块**（第 43-59 行）：

```python
from . import agent
from . import model
from . import formatter
from . import tool
from . import memory
# ... 等等
```

这让 `agentscope.agent` 这样的路径可用。

**2. 定义 `__all__`**（第 159 行开始）：列出 `from agentscope import *` 时导出的名称。

### 子包的 `__init__.py`

每个子包的 `__init__.py` 从内部文件导入公共类：

```python
# agent/__init__.py
from ._agent_base import AgentBase
from ._react_agent_base import ReActAgentBase
from ._react_agent import ReActAgent
from ._user_agent import UserAgent
from ._a2a_agent import A2AAgent
from ._realtime_agent import RealtimeAgent

__all__ = [
    "AgentBase", "ReActAgentBase", "ReActAgent",
    "UserAgent", "A2AAgent", "RealtimeAgent",
]
```

---

## 特殊模块

### `_run_config.py`：全局配置

以 `_` 开头但不在子包中，直接在 `src/agentscope/` 下。它定义了 `_ConfigCls`——全局配置类，通过 `ContextVar` 实现异步安全。

### `_logging.py`：日志配置

另一个顶层内部模块，被 `init()` 调用来设置日志。

### `_version.py`：版本号

存储 `__version__`，被顶层 `__init__.py` 导入。

### `_utils/`：工具函数

```
src/agentscope/_utils/
├── __init__.py
├── _common.py      # 通用工具函数
└── _mixin.py       # DictMixin 等
```

框架内部使用的工具函数，不对外暴露。

---

## 试一试：追踪一个 import 语句

> **官方文档对照**：AgentScope 的模块组织遵循 Python 包的公共/私有分层惯例。官方文档 [Basic Concepts](https://docs.agentscope.io/basic-concepts) 展示了 `Msg`、`Agent`、`Model` 等公共 API 的使用方法，而本章解释了这些公共 API 是如何从 `_` 前缀的内部文件中暴露出来的。
>
> **推荐阅读**：[AgentScope 1.0 论文](https://arxiv.org/pdf/2508.16279) 第 2 节 "Foundational Components" 介绍了 Message、Model、Memory、Tool 四大基础模块的设计目标。

**目标**：理解 `from agentscope.agent import ReActAgent` 背后发生了什么。

**步骤**：

1. 打开 `src/agentscope/agent/__init__.py`，找到 `ReActAgent` 的导入行
2. 追踪到 `src/agentscope/agent/_react_agent.py`，看第 98 行的类定义
3. 再看 `ReActAgent` 继承自 `ReActAgentBase`，在 `_react_agent_base.py` 中
4. `ReActAgentBase` 继承自 `AgentBase`，在 `_agent_base.py` 中

这条继承链跨越了 3 个文件。但用户只需要写 `from agentscope.agent import ReActAgent`。

5. **进阶**：搜索 `__init__.py` 中的 `__all__` 列表，看看每个子包暴露了多少个公共类：

```bash
grep -c "^    \"" src/agentscope/agent/__init__.py src/agentscope/model/__init__.py src/agentscope/tool/__init__.py
```

---

## 检查点

你现在理解了：

- `_` 前缀文件是内部实现，公共 API 通过 `__init__.py` 暴露
- 导入路径是 `agentscope.子包.公共类`，不是直接导入 `_` 文件
- 顶层 `__init__.py` 负责导入子模块和定义 `__all__`
- 这种命名约定定义了框架的公共 API 边界

**自检练习**：

1. 如果你添加了一个新的 Agent 类型 `_my_agent.py`，需要修改哪个文件才能让用户用 `from agentscope.agent import MyAgent` 导入它？
2. `_utils/` 下的工具函数为什么不对外暴露？

---

## 下一章预告

下一章我们打开继承体系，从 `StateModule` → `AgentBase` → `ReActAgentBase` → `ReActAgent` 的四层继承链，看每一层提供了什么能力。
