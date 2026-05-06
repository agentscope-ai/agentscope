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

```python showLineNumbers
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

```python showLineNumbers
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

## 练习题

### 练习 2.1: 安装方式选择 [基础]

**题目**：
小李需要为公司搭建 AgentScope 开发环境，他需要：
1. 在本地开发机上尝试 AgentScope（不涉及贡献代码）
2. 准备一台服务器运行生产环境
3. 克隆代码并准备向社区提交 PR

请分别为这三个场景选择正确的安装方式。

**验证方式**：
检查安装命令是否与文档描述的场景匹配。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

| 场景 | 推荐安装方式 | 命令 |
|------|-------------|------|
| 本地开发机（尝试） | pip 安装（生产模式） | `pip install agentscope` |
| 生产服务器 | pip 安装（生产模式） | `pip install agentscope` |
| 准备提交 PR | 开发模式安装 | `git clone ... && pip install -e ".[dev]"` |

**关键区别**：
- `pip install agentscope`：安装 PyPI 上的稳定版本
- `pip install -e ".[dev]"`：以开发模式安装，代码修改实时生效，且包含 dev 依赖（测试、格式化工具等）
</details>

---

### 练习 2.2: 可选依赖组理解 [基础]

**题目**：
某团队需要构建一个完整的 RAG 问答系统，需要支持 PDF 文档读取和向量检索。请回答：

1. 应该使用哪个可选依赖组？
2. 如果还需要 Redis 作为记忆后端，应该如何安装？
3. 完整安装命令是什么？

**验证方式**：
检查是否理解可选依赖组的组合方式。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

1. **RAG 问答系统需要的依赖组**：`[rag]`
   - 包含：文档读取器 + 向量数据库客户端

2. **Redis 记忆后端需要的依赖组**：`[redis_memory]`
   - 包含：Redis 相关依赖

3. **完整安装命令**：
   ```bash
   pip install agentscope[rag,redis_memory]
   ```

**可选依赖组速查**：
- `[full]` - 完整安装（所有依赖）
- `[models]` - Ollama + Gemini 支持
- `[rag]` - 文档读取 + 向量存储
- `[redis_memory]` - Redis 记忆
- `[realtime]` - 实时语音
- `[a2a]` - A2A 协议支持
</details>

---

### 练习 2.3: API Key 配置 [中级]

**题目**：
以下是小王编写的安装验证脚本，请找出其中的错误并修正：

```python
# test_installation.py
import agentscope

# 初始化
agentscope.init(
    project="test",
    project_name="my-agent",  # 第5行
    api_key="sk-xxxxx"       # 第6行
)

# 检查版本
print(f"AgentScope version: {agentscope.__version__}")
```

**验证方式**：
运行脚本（需要实际环境），检查是否报错。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**错误 1（第 5 行）**：`project_name="my-agent"`
- 错误原因：`project_name` 是旧 API，v1.0.19+ 已更名为 `project`
- 修正：`project="my-agent"`

**错误 2（第 6 行）**：`api_key="sk-xxxxx"`
- 错误原因：v1.0.19+ 已移除 `api_key` 参数，API Key 必须通过环境变量设置
- 修正：删除此行，通过 `export OPENAI_API_KEY="sk-xxxxx"` 设置

**修正后的代码**：
```python
import agentscope
import os

# 设置 API Key（通过环境变量）
os.environ["OPENAI_API_KEY"] = "sk-xxxxx"

# 初始化
agentscope.init(
    project="test"  # 注意：参数名是 project
)

# 检查版本
print(f"AgentScope version: {agentscope.__version__}")
```

**升级注意事项速查**：
| 旧 API | 新 API |
|--------|--------|
| `project_name=...` | `project=...` |
| `api_key=...` | 通过 `os.environ["OPENAI_API_KEY"]` 设置 |
</details>

---

### 练习 2.4: Python-Java 语法对应 [中级]

**题目**：
请将以下 Java 代码翻译成等价的 Python/AgentScope 代码：

```java
// Java 代码
public class MyAgent {
    private String name;
    private Model model;

    public MyAgent(String name, Model model) {
        this.name = name;
        this.model = model;
    }

    public String greet(String userName) {
        return "Hello, " + userName + "!";
    }
}
```

**验证方式**：
对比代码结构和语法，检查是否正确对应。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

```python
# Python/AgentScope 代码
class MyAgent:
    def __init__(self, name: str, model: ChatModelBase) -> None:
        self.name = name      # Python 不需要 private 关键字
        self.model = model    # 约定使用 _ 前缀表示"私有"
    
    def greet(self, user_name: str) -> str:
        return f"Hello, {user_name}!"  # 使用 f-string 格式化
```

**关键语法对比**：

| Java | Python/AgentScope |
|------|-------------------|
| `private String name` | `self.name = name`（约定 `_name` 表示私有） |
| `public MyAgent(...)` | `def __init__(self, ...)` |
| `String userName` | `user_name: str`（snake_case + type hint） |
| `"Hello, " + name + "!"` | `f"Hello, {name}!"` |
| `void` | `-> None` |

**补充说明**：
- Python 使用 snake_case（而非 camelCase）作为变量/函数命名约定
- Type hints 可以用 `mypy` 进行静态检查
- AgentScope 的 Agent 通常继承 `ReActAgent`，这里仅为语法对比
</details>

## 2.9 下一步

- [第三章：快速入门](03_quickstart.md) - 构建你的第一个 Agent
