# 环境搭建与项目安装

> **Level 2**: 能运行项目  
> **前置要求**: [AgentScope 概述](../00-architecture-overview/00-overview.md)  
> **后续章节**: [运行第一个 Agent](./01-first-agent.md)

---

## 学习目标

学完之后，你能：
- 在自己的机器上安装 AgentScope 及其依赖
- 配置好至少一个 LLM 提供商的 API Key
- 用 `pip install` 或源码方式安装项目
- 验证安装是否成功

---

## 背景问题

AgentScope 是一个 **Python 3.10+** 项目，使用 `setuptools` 作为构建后端，`pip` 作为包管理器。

与 Java 项目的关键差异：

| 操作 | Python (AgentScope) | Java (Maven) |
|------|-------------------|--------------|
| 安装依赖 | `pip install -e ".[full]"` | `mvn install` |
| 依赖声明 | `pyproject.toml` | `pom.xml` |
| 版本管理 | `_version.py` 文件 | pom.xml 中的 `<version>` |
| 包结构 | 目录 + `__init__.py` | 目录 + `package` 声明 |
| 运行测试 | `pytest tests/` | `mvn test` |

---

## 源码入口

**安装相关的关键文件**:

| 文件 | 作用 |
|------|------|
| `pyproject.toml:1-60` | 项目元数据、依赖声明 |
| `pyproject.toml:61-160` | optional-dependencies 分组 |
| `src/agentscope/_version.py:4` | 版本号: `1.0.19.post1` |
| `src/agentscope/__init__.py` | 包初始化入口 |

**版本号获取方式** (`pyproject.toml:213`):
```toml
[tool.setuptools.dynamic]
version = {attr = "agentscope._version.__version__"}
```

版本号是**动态**的，在构建时从 `_version.py` 读取。

---

## 安装步骤

### 方式 1：从 PyPI 安装（推荐给使用者）

```bash
# 最小安装（只有核心功能）
pip install agentscope

# 常用安装（含 OpenAI 和 DashScope）
pip install "agentscope[full]"
```

### 方式 2：源码安装（推荐给 Contributor）

```bash
# 克隆仓库
git clone https://github.com/agentscope-ai/agentscope
cd agentscope

# 开发模式安装（修改源码自动生效）
pip install -e "agentscope[full]"

# 安装开发工具（pre-commit, pytest 等）
pip install -e "agentscope[dev]"
```

`-e` (editable mode) 意味着你对 `src/` 下源码的任何修改会**立即生效**，不需要重新安装。

### 可选依赖分组

**文件**: `pyproject.toml:44-160`

| 分组 | 安装命令 | 包含的功能 |
|------|---------|-----------|
| `a2a` | `pip install "agentscope[a2a]"` | A2A 协议支持 |
| `realtime` | `pip install "agentscope[realtime]"` | 实时语音 |
| `gemini` | `pip install "agentscope[gemini]"` | Google Gemini 模型 |
| `ollama` | `pip install "agentscope[ollama]"` | 本地 Ollama 模型 |
| `models` | `pip install "agentscope[models]"` | 包含 ollama + gemini |
| `tokens` | `pip install "agentscope[tokens]"` | HuggingFace tokenizer |
| `redis_memory` | `pip install "agentscope[redis_memory]"` | Redis 记忆后端 |
| `rag` | `pip install "agentscope[rag]"` | RAG 知识库 |
| `evaluate` | `pip install "agentscope[evaluate]"` | 评估系统 |
| `full` | `pip install "agentscope[full]"` | 除 dev 外的所有功能 |
| `dev` | `pip install "agentscope[dev]"` | 开发工具链 |

---

## API Key 配置

AgentScope 支持多种模型提供商，每个需要独立的 API Key：

```bash
# OpenAI
export OPENAI_API_KEY="sk-your-key"

# DashScope (阿里云)
export DASHSCOPE_API_KEY="your-key"

# Anthropic
export ANTHROPIC_API_KEY="sk-ant-your-key"

# Google Gemini
export GOOGLE_API_KEY="your-key"
```

**AgentScope 中如何使用** — 以 DashScope 为例（来自 `examples/agent/react_agent/main.py:29-32`）：

```python
from agentscope.model import DashScopeChatModel

model = DashScopeChatModel(
    api_key=os.environ.get("DASHSCOPE_API_KEY"),
    model_name="qwen-max",
    stream=True,
)
```

**设计要点**: AgentScope 的模型类不会自动读取环境变量。你需要显式传入 `api_key`。这是因为：
1. 不同模型可能有不同的环境变量名
2. 在代码中显式传入让依赖关系更清晰
3. 支持从配置中心、密钥管理服务等非环境变量来源获取

---

## 验证安装

```bash
# 1. 检查版本
python -c "import agentscope; print(agentscope.__version__)"
# 输出: 1.0.19.post1

# 2. 检查关键子模块是否可导入
python -c "
from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit
from agentscope.memory import InMemoryMemory
print('All modules OK')
"

# 3. 运行官方示例
cd examples/agent/react_agent
python main.py
```

---

## 工程经验

### 为什么用 `pyproject.toml` 而不是 `setup.py`？

AgentScope 使用 `pyproject.toml`（PEP 621 标准）声明项目配置，构建后端是 `setuptools`。

```toml
# pyproject.toml:208-212
[build-system]
requires = ["setuptools>=45", "wheel"]
build-backend = "setuptools.build_meta"
```

**原因**:
1. `pyproject.toml` 是 Python 生态的标准配置格式（PEP 621）
2. 相比 `setup.py`（可执行脚本），`pyproject.toml`（声明式）更安全（不会在 `pip install` 时执行任意代码）
3. `setup.cfg` 也可以用，但 `pyproject.toml` 是最新的统一标准

### `post1` 版本号的含义

当前版本 `1.0.19.post1`：
- `1.0.19` — 主版本号
- `.post1` — 发布后的第 1 次修订（Post-release），通常用于修复打包问题但不改变功能

这是 Python 生态中常见的版本约定（PEP 440）。

### 常见安装问题

**问题 1: `pip install` 报 "No matching distribution found"**

原因：AgentScope 要求 Python >= 3.10（`pyproject.toml:18`）

```bash
python --version  # 必须 >= 3.10
```

**问题 2: `pip install -e` 后导入仍然报错**

原因：依赖未完全安装。确保使用了正确的 extras：

```bash
pip install -e "agentscope[full]"  # 不是 pip install -e .
```

**问题 3: pre-commit 钩子无法运行**

```bash
# 需要先安装 pre-commit
pip install pre-commit
pre-commit install  # 在项目根目录运行
```

---

## Contributor 指南

### 干净安装检查清单

```bash
# 1. 创建虚拟环境
python3.10 -m venv .venv
source .venv/bin/activate

# 2. 安装项目 + 开发工具
pip install -e "agentscope[dev]"

# 3. 运行全部测试
pytest tests/ --forked

# 4. 类型检查
mypy src/agentscope/

# 5. 预提交检查
pre-commit run --all-files
```

### 适合新手的环境相关 PR

- 添加对新 Python 版本（如 3.13）的支持测试
- 改进 `pyproject.toml` 中的可选依赖分组
- 添加 Docker 开发环境配置
- 修复特定平台（macOS ARM64 / Windows）上的安装问题

---

## 下一步

环境准备好了？前往 [运行第一个 Agent](./01-first-agent.md)。


---

## 工程现实与架构问题

### 技术债 (安装/依赖)

| 位置 | 问题 | 影响 | 优先级 |
|------|------|------|--------|
| `pyproject.toml` | 可选依赖过多 | `pip install agentscope[full]` 可能安装不需要的包 | 中 |
| 跨平台支持 | Windows/macOS ARM64 测试不足 | 特定平台可能安装失败 | 中 |
| `DashScope` | 国内模型优先但无离线模式 | 无法在没有网络的环境使用 | 低 |

**[HISTORICAL INFERENCE]**: AgentScope 优先保证国内开发者体验（DashScope 优先），但这导致了跨平台兼容性的技术债。

### 性能考量

```bash
# 安装时间估算
pip install agentscope:           ~10-30s (最小依赖)
pip install agentscope[full]:     ~60-180s (全部依赖)

# Docker 镜像大小
agentscope base image:            ~500MB
agentscope + CUDA:               ~2-3GB
```

### 渐进式重构方案

```bash
# 方案: 按需安装
pip install agentscope              # 最小安装
pip install agentscope[openai]    # 按需添加
pip install agentscope[dashscope]
pip install agentscope[all]       # 全部依赖
```

