# 第二章：环境搭建

## 学习目标

- 能够正确安装和配置 Python 开发环境（conda 或 venv）
- 掌握 AgentScope 的多种安装方式及其可选依赖组
- 完成 IDE 配置，能够运行验证脚本确认安装成功
- 正确配置 LLM API Keys，理解环境变量和代码配置两种方式

## 2.1 Python 环境准备

作为 Java 开发者，如果你熟悉 Maven/Gradle，Python 的包管理会让你想起 Node.js 的 npm。

### 推荐使用 conda (类似 Java 的 jenv/JDK 版本管理)

```bash
# 安装 conda (如果你还没有)
brew install conda  # macOS
# Windows: 下载 Miniconda 或 Anaconda

# 创建 Python 3.10+ 环境 (类似 java -version 管理)
conda create -n agentscope python=3.11
conda activate agentscope
```

### 或者使用 venv (类似 Maven wrapper)

```bash
cd agentscope
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# Windows: .venv\Scripts\activate
```

## 2.2 安装 AgentScope

### 方式一：pip 安装 (推荐生产使用)

```bash
pip install agentscope
```

### 方式二：开发模式安装 (适合贡献者)

```bash
git clone https://github.com/agentscope-ai/agentscope.git
cd agentscope
pip install -e ".[dev]"  # 类似 mvn install -DskipTests
```

### 可选依赖组

AgentScope 使用可选的依赖组来减少安装体积：

```bash
# 完整安装 (类似 Spring Boot full dependency)
pip install agentscope[full]

# 仅核心功能
pip install agentscope

# 带扩展模型支持 (Ollama + Gemini)
pip install agentscope[models]

# 带记忆后端 (Redis / Mem0 / Reme)
pip install agentscope[memory]

# 带 Redis 记忆
pip install agentscope[redis_memory]

# 带 RAG 功能 (文档读取器 + 向量数据库)
pip install agentscope[rag]

# 带实时语音
pip install agentscope[realtime]

# 带调优功能
pip install agentscope[tuner]

# 带 A2A 协议支持
pip install agentscope[a2a]

# 带评估功能
pip install agentscope[evaluate]
```

## 2.3 IDE 设置

### IntelliJ IDEA (你可能已经在用)

IntelliJ IDEA 原生支持 Python：

1. **安装 Python 插件**
   - Settings → Plugins → 搜索 "Python"
   - 安装 JetBrains 的 Python 插件

2. **配置项目 SDK**
   - File → Project Structure → Platform Settings → SDKs
   - 添加 Python SDK (选择你创建的 conda/venv 环境)

3. **设置项目根目录**
   - File → Project Structure → Project
   - 设置 Project SDK 为 Python

### VS Code (轻量替代)

```bash
code agentscope --extensions ms-python.python
```

关键插件：
- Python (Microsoft)
- Pylance (类型检查)
- Python Debugger

## 2.4 验证安装

```python
# 创建 test_installation.py

import agentscope

# 初始化 (类似 Spring Boot 启动)
agentscope.init(project="test", logging_path="./workspace")

# 检查版本
print(f"AgentScope version: {agentscope.__version__}")

# 列出可用组件
print(f"Available agents: {agentscope.agent.__all__}")
print(f"Available models: {agentscope.model.__all__}")
```

运行：

```bash
python test_installation.py
```

## 2.5 配置 API Keys

AgentScope 需要调用外部 LLM API，你需要配置相应的 Keys：

### 方式一：环境变量 (推荐)

```bash
# ~/.bashrc 或 ~/.zshrc

# OpenAI (最常用)
export OPENAI_API_KEY="sk-xxxxx"

# Anthropic (Claude)
export ANTHROPIC_API_KEY="sk-ant-xxxxx"

# 阿里云 DashScope
export DASHSCOPE_API_KEY="sk-xxxxx"

# Ollama (本地模型)
export OLLAMA_BASE_URL="http://localhost:11434"
```

### 方式二：在代码中配置

```python
import os
os.environ["OPENAI_API_KEY"] = "sk-xxxxx"

# 或者
agentscope.init(
    project="my-project",
)
```

## 2.6 项目目录结构规范

AgentScope 推荐的目录结构：

```
my_agent_project/
├── src/                    # 源码目录
│   ├── __init__.py
│   ├── agents/             # Agent 定义
│   │   └── my_agent.py
│   ├── tools/              # 工具函数
│   │   └── my_tools.py
│   └── prompts/            # 提示词模板
│       └── my_prompts.py
├── configs/                # 配置文件 (类似 application.yml)
│   └── model_config.json
├── workspace/              # AgentScope 工作目录
│   └── (运行时生成)
├── tests/                  # 测试
│   └── test_my_agent.py
├── requirements.txt        # Python 依赖
└── README.md
```

## 2.7 Java 开发者的 Python 备忘单

| Java 概念 | Python 对应 | 示例 |
|-----------|-------------|------|
| `public class` | `class` (默认公开) | `class MyAgent:` |
| `private` | `_` 前缀约定 | `self._private_method()` |
| `protected` | `__` 双下划线 (name mangling) | `self.__protected()` |
| `interface` | Abstract Base Class / Protocol | `from abc import ABC` |
| `@Autowired` | 直接传参 / 依赖注入 | `def __init__(self, model):` |
| `List<String>` | `List[str]` / `list[str]` | `names: list[str] = []` |
| `Map<String, Object>` | `Dict[str, Any]` | `config: dict = {}` |
| `void` | `None` | `def do_something() -> None:` |
| `System.out.println()` | `print()` | `print("hello")` |
| `Optional<T>` | `Optional[T]` / `T \| None` | `name: str \| None = None` |
| `try-catch` | `try-except` | `try: ... except Exception:` |
| `{}` 格式化 | `f-string` | `f"Hello {name}"` |
| `Maven pom.xml` | `pyproject.toml` / `requirements.txt` | - |
| `mvn test` | `pytest` / `python -m unittest` | `pytest tests/` |

## 2.8 总结

本章完成了 AgentScope 开发环境的搭建：

1. **Python 环境**：推荐使用 conda 管理 Python 3.10+ 环境，类似 Java 的 jenv
2. **安装方式**：`pip install agentscope` 生产安装，`pip install -e ".[dev]"` 开发模式安装
3. **可选依赖**：`[full]` 完整安装，`[models]` 扩展模型，`[rag]` RAG 功能，`[realtime]` 实时语音等
4. **IDE 配置**：IntelliJ IDEA 和 VS Code 均可，需配置 Python SDK
5. **API Keys**：通过环境变量配置（推荐）或在代码中设置

## 2.9 下一步

- [第三章：快速入门](03_quickstart.md) - 构建你的第一个 Agent
