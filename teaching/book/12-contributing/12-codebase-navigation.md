# 源码导航地图

> **Level 8**: 能提交高质量 PR
> **前置要求**: [PR 流程与代码规范](./12-how-to-contribute.md)
> **后续章节**: [调试指南](./12-debugging-guide.md)

---

## 学习目标

学完本章后，你能：
- 快速定位 AgentScope 源码的各个模块
- 理解每个目录的职责边界
- 找到特定功能对应的源码文件
- 学会阅读大型 Python 项目的目录结构

---

## 背景问题

AgentScope 有 33 个源码子目录和 774+ 行的核心文件。新 Contributor 面对的第一个障碍不是代码逻辑，而是**"我要改的功能在哪个文件？"**。本章提供一套可操作的导航方法——从功能需求反推源码位置。

---

## 源码入口

| 项目 | 值 |
|------|-----|
| **源码根** | `src/agentscope/` (33 个子目录) |
| **模块清单** | `src/agentscope/__init__.py` (公开 API 导出) |
| **仓库地图** | [repository-map.md](../repository-map.md) (PHASE0 文档) |

---

## 源码目录结构

```
src/agentscope/
├── agent/              # Agent 实现
│   ├── _agent_base.py      # Agent 基类
│   ├── _react_agent.py     # ReActAgent 实现
│   ├── _user_agent.py      # UserAgent
│   └── _realtime_agent.py  # 实时语音 Agent
│
├── model/              # LLM 模型适配
│   ├── _model_base.py      # 基类
│   ├── _openai_model.py    # OpenAI 适配
│   └── ...
│
├── formatter/          # 消息格式化
│   ├── _formatter_base.py   # 基类
│   └── _openai_formatter.py
│
├── tool/               # 工具系统
│   ├── _toolkit.py          # 工具注册与调用
│   └── _response.py         # 工具响应
│
├── memory/             # 记忆系统
│   ├── _working_memory/     # 工作记忆
│   └── _long_term_memory/  # 长期记忆
│
├── pipeline/           # 管道编排
│   ├── _class.py            # Pipeline 类
│   └── _msghub.py          # MsgHub
│
├── message/            # 消息系统
│   ├── _message_base.py     # Msg 类
│   └── _message_block.py    # ContentBlock
│
├── rag/                # RAG 系统
├── evaluate/           # 评估系统
├── tuner/              # 调优系统
├── tts/                # TTS 系统
├── mcp/                # MCP 协议
├── a2a/                # A2A 协议
├── tracing/            # 追踪系统
├── session/            # 会话管理
└── runtime/            # 运行时
```

---

## 快速定位指南

### 想找... | 去这里
---|---
Agent 基类 | `agent/_agent_base.py`
ReActAgent 实现 | `agent/_react_agent.py`
工具注册 | `tool/_toolkit.py`
消息格式 | `formatter/_openai_formatter.py`
Pipeline | `pipeline/_class.py`
MsgHub | `pipeline/_msghub.py`

---

## 模块职责详解

### agent/ - Agent 实现

**核心类**: `AgentBase` → `ReActAgentBase` → `ReActAgent`

| 文件 | 类/函数 | 职责 |
|------|---------|------|
| `_agent_base.py` | `AgentBase` | 所有 Agent 的基类 |
| `_react_agent.py` | `ReActAgent` | 推理-行动循环 |
| `_user_agent.py` | `UserAgent` | 人工输入处理 |

### model/ - 模型适配

**核心类**: `ChatModelBase`

| 文件 | 类 | 职责 |
|------|---|------|
| `_model_base.py` | `ChatModelBase` | 统一接口 |
| `_openai_model.py` | `OpenAIChatModel` | OpenAI 适配 |
| `_model_response.py` | `ChatResponse` | 响应封装 |

### tool/ - 工具系统

| 文件 | 类/函数 | 职责 |
|------|---------|------|
| `_toolkit.py` | `Toolkit` | 工具注册、调用、Schema 生成 |
| `_response.py` | `ToolResponse` | 工具响应封装 |

### pipeline/ - 管道编排

| 文件 | 类 | 职责 |
|------|---|------|
| `_class.py` | `SequentialPipeline`, `FanoutPipeline` | 管道执行 |
| `_msghub.py` | `MsgHub` | 发布-订阅 |

---

## 源码阅读技巧

### 1. 从入口开始

```python
# Agent 入口
agent = ReActAgent(...)
result = await agent(msg)

# 追踪: agent.reply() → _reasoning() → _acting()
```

### 2. 使用 IDE 导航

- **PyCharm**: Ctrl+B 跳转到定义
- **VS Code**: Cmd+点击 跳转

### 3. 查找使用关系

```bash
# 查找某个函数的所有调用
grep -r "call_tool_function" src/agentscope/
```

---

## 常用命令

```bash
# 类型检查
mypy src/agentscope/

# 代码格式化
black src/agentscope/

# 运行测试
pytest tests/ -k "test_name"

# 生成覆盖率报告
pytest --cov=src/agentscope tests/
```

---

## 下一步

接下来学习 [调试指南](./12-debugging-guide.md)。


---

## 工程现实与架构问题

### 技术债 (源码级)

| 位置 | 问题 | 影响 | 优先级 |
|------|------|------|--------|
| `src/agentscope/` | 目录结构无强制约束 | 新增模块位置不明确 | 低 |
| `_` 前缀约定无强制检查 | 内部模块被意外导入 | 暴露私有 API | 中 |
| `__init__.py` | 公开 API 定义分散 | 难以确定公共接口 | 中 |

**[HISTORICAL INFERENCE]**: AgentScope 的目录结构是基于早期设计，随着功能增长未及时重构。`_` 前缀约定是 Python 社区习惯，非语言级别强制。

### 性能考量

```bash
# 模块导入延迟估算
agent/          ~50ms (基础 Agent 类)
model/          ~100ms (模型适配器，加载较慢)
tool/           ~30ms (工具注册)
memory/         ~20ms (记忆系统)
pipeline/       ~10ms (管道编排)

# IDE 索引影响
完整索引 src/agentscope/ ~30-60s (首次)
增量索引            ~5-10s
```

### 渐进式重构方案

```python
# 方案 1: 添加模块结构检查
import ast
import os
from pathlib import Path

def check_module_structure(src_dir: str) -> list[str]:
    """检查源码目录结构是否符合规范"""
    issues = []
    src_path = Path(src_dir)

    for py_file in src_path.rglob("*.py"):
        # 检查内部模块是否有 _ 前缀
        if py_file.stem.startswith("test_"):
            continue
        if py_file.stem not in ("__init__", "CONSTANTS"):
            if not py_file.stem.startswith("_"):
                issues.append(f"内部模块缺少 _ 前缀: {py_file}")

    return issues

# 方案 2: 自动化生成公开 API 文档
def generate_public_api(module_path: str) -> dict:
    """从 __init__.py 提取公开 API"""
    init_file = Path(module_path) / "__init__.py"
    if not init_file.exists():
        return {}

    tree = ast.parse(init_file.read_text())
    public_api = {
        "classes": [],
        "functions": [],
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            public_api["classes"].append(node.name)
        elif isinstance(node, ast.FunctionDef):
            if not node.name.startswith("_"):
                public_api["functions"].append(node.name)

    return public_api
```

