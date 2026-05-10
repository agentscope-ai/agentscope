# 第十九章：扩展准备——搭建开发环境与理解工程规范

**难度**：入门

> 前两卷我们追踪了源码、拆解了设计模式。从本章开始，你不再是读者，而是贡献者——我们要在框架上动手构建真实的扩展。但在写第一行扩展代码之前，必须先把开发环境搭好、理解测试策略、掌握代码规范。本章是一切实战的前置准备。

---

## 1. 实战目标

完成本章后，你将：

1. 拥有一个可以本地运行测试的 AgentScope 开发环境
2. 理解 `tests/` 目录的组织方式和测试运行方法
3. 掌握 pre-commit 钩子的配置和各检查项的含义
4. 了解代码风格、docstring、导入规范
5. 清楚从 fork 到 PR 合并的完整流程

---

## 2. 开发环境搭建

### 2.1 Fork 与 Clone

```bash
# 1. 在 GitHub 上 fork agentscope-ai/agentscope
# 2. Clone 你自己的 fork
git clone https://github.com/<your-username>/agentscope.git
cd agentscope

# 3. 添加上游仓库（用于同步最新代码）
git remote add upstream https://github.com/agentscope-ai/agentscope.git
```

### 2.2 创建开发分支

AgentScope 使用 Conventional Commits 规范。分支命名建议与即将做的工作对应：

```bash
# 功能开发
git checkout -b feat/my-new-memory

# 修复问题
git checkout -b fix/memory-leak
```

### 2.3 安装开发依赖

AgentScope 的 `pyproject.toml` 定义了 `[dev]` 可选依赖组，包含完整依赖加开发工具。安装命令：

```bash
pip install -e ".[dev]"
```

这个命令做了三件事：

1. **`-e`（editable 模式）**：以可编辑模式安装，修改 `src/agentscope/` 下的代码立即生效，无需重新安装
2. **`.[full]`**（dev 组内包含）：安装所有可选依赖——模型、内存、RAG、评估等
3. **开发工具**：`pre-commit`、`pytest`、`pytest-asyncio`、`pytest-forked`、`mypy`、`flake8`、`black`、`pylint` 等

`pyproject.toml` 中的依赖组织如下：

```toml
# 核心依赖（pip install agentscope 即获得）
[project]
dependencies = [
    "openai", "anthropic", "dashscope", "tiktoken",
    "sqlalchemy", "mcp>=1.13", ...
]

# 可选依赖（按功能分组）
[project.optional-dependencies]
redis_memory = ["redis"]
rag = ["agentscope[readers]", "agentscope[vdbs]"]
full = ["agentscope[a2a]", "agentscope[models]", ...]
dev = ["agentscope[full]", "pre-commit", "pytest", ...]
```

如果你只需要特定模块的开发环境，可以精确安装。例如只开发 memory 相关功能：

```bash
pip install -e ".[memory,dev]"
```

### 2.4 安装 Pre-commit 钩子

```bash
pre-commit install
```

安装后，每次 `git commit` 会自动运行代码质量检查。如果检查失败，提交会被阻止。

也可以手动运行：

```bash
# 对所有文件运行
pre-commit run --all-files

# 对暂存文件运行（模拟 commit 时行为）
pre-commit run
```

### 2.5 IDE 配置要点

无论使用 VS Code 还是 PyCharm，关键配置：

- **Black 格式化器**，参数 `--line-length=79`（不是默认的 88）
- **Mypy 类型检查**，启用 `--disallow-untyped-defs`
- **Pylint**，参考 `.pre-commit-config.yaml` 中的禁用规则列表

---

## 3. 测试策略

### 3.1 测试目录结构

所有测试文件位于项目根目录的 `tests/` 下，按模块命名（`<模块名>_test.py`）：

```
tests/
├── react_agent_test.py              # ReAct Agent 完整测试
├── memory_test.py                   # 记忆系统（含压缩、ReMe）
├── memory_compression_test.py
├── memory_reme_test.py
├── toolkit_basic_test.py            # 工具系统（基础/异步/中间件）
├── toolkit_async_execution_test.py
├── toolkit_middleware_test.py
├── model_openai_test.py             # 模型适配（5 个厂商）
├── model_anthropic_test.py
├── model_dashscope_test.py
├── model_gemini_test.py
├── model_ollama_test.py
├── formatter_openai_test.py         # 格式化（6 种格式）
├── formatter_anthropic_test.py
├── ...                              # 其余 formatter 测试
├── pipeline_test.py                 # Pipeline 流程
├── rag_knowledge_test.py            # RAG 系统
├── tracing_test.py                  # 追踪系统
├── session_test.py                  # 会话管理
├── realtime_openai_test.py          # 实时语音
├── a2a_agent_test.py                # A2A 协议
├── ...                              # token / tts / tuner 等
└── config_test.py                   # 配置系统
```

完整列表约 60 个测试文件，覆盖框架所有主要模块。

### 3.2 运行测试

```bash
# 运行全部测试
pytest tests/

# 运行单个模块的测试
pytest tests/memory_test.py

# 运行匹配关键字的测试
pytest tests/ -k "toolkit"

# 运行特定测试类或方法
pytest tests/toolkit_basic_test.py::ToolkitTest::test_register

# 隔离运行（每个测试独立进程，避免状态泄漏）
pytest tests/ --forked

# 显示详细输出
pytest tests/ -v

# 显示 print 输出（调试用）
pytest tests/ -s
```

### 3.3 CI 测试矩阵

项目的 GitHub Actions（`.github/workflows/unittest.yml`）在以下矩阵上运行测试：

- **操作系统**：Ubuntu、Windows、macOS
- **Python 版本**：3.10、3.11、3.12

```yaml
# .github/workflows/unittest.yml
strategy:
  matrix:
    os: [ubuntu-latest, windows-latest, macos-15]
    python-version: ['3.10', '3.11', '3.12']
```

这意味着你的代码需要在三个操作系统和三个 Python 版本上通过测试。

### 3.4 测试编写模式

AgentScope 的测试大量使用 `unittest.IsolatedAsyncioTestCase`，因为框架核心是异步的。典型模式：

```python
from unittest.async_case import IsolatedAsyncioTestCase

class MyMemoryTest(IsolatedAsyncioTestCase):
    """The memory tests."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        # 初始化测试数据
        self.msgs = [Msg("user", "hello", "user")]

    async def test_basic_operations(self) -> None:
        """Test basic memory operations."""
        memory = InMemoryMemory()
        await memory.add(self.msgs[0])
        result = await memory.get_memory()
        self.assertEqual(len(result), 1)
```

关键要点：

1. **继承 `IsolatedAsyncioTestCase`**：用于异步测试
2. **用 `asyncSetUp` 做初始化**：每个测试方法前执行
3. **方法名以 `test_` 开头**：pytest 和 unittest 都会自动发现
4. **用 `self.assertEqual`/`assertIsInstance`** 等 assert 方法

### 3.5 为新功能编写测试

当你在后续章节中为框架添加扩展时，测试文件应遵循以下规范：

```python
# tests/my_new_feature_test.py
# -*- coding: utf-8 -*-
"""Test my new feature in agentscope."""

from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.my_module import MyClass


class MyFeatureTest(IsolatedAsyncioTestCase):
    """Test my new feature."""

    async def test_basic_functionality(self) -> None:
        """Test the basic functionality."""
        obj = MyClass()
        result = await obj.do_something()
        self.assertIsNotNone(result)
```

---

## 4. 代码规范

### 4.1 文件命名：`_` 前缀惯例

`src/agentscope/` 下的所有 Python 文件使用 `_` 前缀。例如：

```
src/agentscope/agent/
├── __init__.py           # 公共导出
├── _agent_base.py        # 内部实现
├── _react_agent.py       # 内部实现
└── _user_agent.py        # 内部实现
```

用户不会直接 `from agentscope.agent._react_agent import ReActAgent`，而是通过 `__init__.py` re-export：

```python
# src/agentscope/agent/__init__.py
from ._react_agent import ReActAgent

__all__ = ["ReActAgent"]
```

**规则**：你新建的文件也必须遵循 `_` 前缀命名。

### 4.2 导入规范：Lazy Import

AgentScope 遵循延迟导入原则——第三方库不在文件顶部导入，而在使用点导入：

```python
# 错误：文件顶部导入第三方库
import redis

class RedisMemory(MemoryBase):
    def __init__(self):
        self.client = redis.Redis()

# 正确：使用时才导入
class RedisMemory(MemoryBase):
    def __init__(self):
        import redis
        self.client = redis.Redis()
```

这样做的好处：`import agentscope` 时不会加载用户不需要的依赖。核心依赖（`pyproject.toml` 中 `dependencies` 列表里的）可以在顶部导入。

对于需要条件导入基类的场景，使用工厂模式：

```python
def get_my_class():
    from some_lib import BaseClass

    class MyClass(BaseClass):
        ...

    return MyClass
```

### 4.3 Docstring 规范

所有类和公共方法必须有 docstring，使用以下格式：

```python
def process_message(
    msg: Msg,
    max_tokens: int | None = None,
) -> list[Msg]:
    """Process the message and return results.

    Args:
        msg (`Msg`):
            The input message to process.
        max_tokens (`int | None`, optional):
            The maximum number of tokens. Defaults to None.

    Returns:
        `list[Msg]`:
            The processed messages.
    """
    ...
```

注意要点：

1. 描述用英文
2. 参数类型用反引号包裹：`` `int | None` ``
3. 每个参数单独一行，缩进对齐
4. 返回值部分描述类型和含义

### 4.4 代码格式化：Black + Flake8 + Pylint

Pre-commit 配置（`.pre-commit-config.yaml`）中包含以下检查：

| 工具 | 配置 | 作用 |
|------|------|------|
| **Black** | `--line-length=79` | 代码格式化 |
| **Flake8** | `--extend-ignore=E203` | 风格检查 |
| **Pylint** | 大量 `--disable` 规则 | 深度代码分析 |
| **Mypy** | `--disallow-untyped-defs` 等 | 类型检查 |
| **check-ast** | - | 语法有效性 |
| **trailing-whitespace** | - | 行尾空白 |
| **detect-private-key** | - | 私钥泄露检测 |
| **check-docstring-first** | - | docstring 位置 |

**Black 的关键参数**：行长度 79（而非 Black 默认的 88），这是 Pylint 的默认最大行长。

**Mypy 的关键参数**：

```yaml
args: [
    --disallow-untyped-defs,        # 所有函数必须有类型注解
    --disallow-incomplete-defs,     # 参数和返回值都必须有类型
    --ignore-missing-imports,       # 忽略无 stub 的第三方库
    --follow-imports=skip,          # 不追踪导入链
]
```

这意味着你的代码**必须有完整的类型注解**。

### 4.5 Pylint 禁用规则

Pylint 禁用了 20+ 条规则（完整列表见 `.pre-commit-config.yaml`），关键几条：

- `C0415`：允许 lazy import（不在文件顶部导入第三方库）
- `E0401`：允许可选依赖未安装时的 import 错误
- `R0913`：允许方法参数较多
- `R0801`：允许不同适配器之间结构相似

**注意**：在 pre-commit 检查中，除非是 Agent 系统提示词参数（避免 `\n` 格式化问题），否则不应该跳过检查。

---

## 5. PR 流程

### 5.1 从 Issue 开始

提交 PR 之前，应该先有对应的 Issue：

```bash
# 1. 在 GitHub 上创建 Issue，描述你要解决的问题
# 2. 如果已有相关 Issue，在 Issue 下评论表示你要认领
```

查看项目计划：
- [Projects 页面](https://github.com/orgs/agentscope-ai/projects/2)
- [Roadmap 标签的 Issue](https://github.com/agentscope-ai/agentscope/issues?q=is%3Aissue+state%3Aopen+label%3ARoadmap)

### 5.2 开发与提交

```bash
# 确保在正确的分支上
git checkout -b feat/my-feature

# 开发...编写代码和测试

# 运行 pre-commit 检查
pre-commit run --all-files

# 运行测试
pytest tests/

# 提交（遵循 Conventional Commits）
git add src/agentscope/... tests/...
git commit -m "feat(memory): add compression support for RedisMemory"
```

提交信息格式：`<type>(<scope>): <description>`

允许的 type：`feat`、`fix`、`docs`、`refactor`、`test`、`ci`、`chore`、`perf`、`style`、`build`、`revert`。

### 5.3 推送与创建 PR

```bash
# 推送到你的 fork
git push origin feat/my-feature
```

在 GitHub 上创建 Pull Request。PR 标题也必须遵循 Conventional Commits 格式。项目有自动化校验——PR 标题不符合格式会被 GitHub Actions 拒绝（`.github/workflows/pr-title-check.yml`）。

### 5.4 PR 模板检查清单

创建 PR 时，模板（`.github/PULL_REQUEST_TEMPLATE.md`）要求确认：

- [ ] 代码已通过 `pre-commit run --all-files`
- [ ] 所有测试通过
- [ ] Docstring 使用规范格式
- [ ] 相关文档已更新
- [ ] 代码准备好接受审查

### 5.5 CI 自动检查

PR 提交后，以下 CI 检查会自动运行：

1. **Pre-commit 检查**（`.github/workflows/pre-commit.yml`）：Black、Flake8、Pylint、Mypy 等全部工具
2. **单元测试**（`.github/workflows/unittest.yml`）：在 Ubuntu/Windows/macOS + Python 3.10/3.11/3.12 上运行
3. **PR 标题检查**（`.github/workflows/pr-title-check.yml`）：验证标题符合 Conventional Commits

---

## 6. 动手练习

### 练习 1：搭建开发环境

1. Fork 并 clone 仓库
2. 安装开发依赖：`pip install -e ".[dev]"`
3. 运行 `python -c "import agentscope; print(agentscope.__version__)"` 确认安装成功
4. 安装 pre-commit：`pre-commit install`
5. 手动运行一次：`pre-commit run --all-files`

### 练习 2：运行测试并理解输出

```bash
pytest tests/memory_test.py -v
pytest tests/toolkit_basic_test.py -v
pytest tests/ -k "formatter" -v
```

观察输出，确认测试通过。如果有失败，检查是否缺少可选依赖。

### 练习 3：模拟一次提交流程

```bash
git checkout -b test/dev-setup-practice
echo "# Practice comment" >> tests/config_test.py
git add tests/config_test.py
git commit -m "test: verify dev setup"
# 观察 pre-commit 的行为
# 完成后清理
git checkout cmbt
git branch -D test/dev-setup-practice
```

### 练习 4：阅读代码审查指南

阅读 `.github/copilot-instructions.md`，列出你觉得最重要的三条 [MUST] 规则。

---

## 7. PR 检查清单

在完成本章的所有练习后，用以下清单验证你的准备状态：

- [ ] 开发环境已搭建，`import agentscope` 可正常运行
- [ ] `pre-commit run --all-files` 通过
- [ ] `pytest tests/memory_test.py` 通过
- [ ] `pytest tests/toolkit_basic_test.py` 通过
- [ ] 理解 Conventional Commits 格式
- [ ] 知道 `_` 前缀命名惯例
- [ ] 知道 lazy import 原则
- [ ] 知道 docstring 格式要求
- [ ] 已阅读 `.github/copilot-instructions.md`

---

## 8. 下一章预告

下一章（ch20）将进入第一个实战项目——为 AgentScope 编写一个自定义工具函数，并完整走通注册、测试、格式化、提交的流程。
