# 第 21 章 开发环境搭建

> **卷三：造一个新齿轮**。从本章开始，手把手教你扩展 AgentScope。
> 本章你将把开发环境完全搭好，为后续章节做准备。

---

## 21.1 目标

搭建一个可以修改源码、运行测试、提交 PR 的开发环境。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 21.2 Step 1: Clone 和安装

```bash
# 1. 克隆仓库
git clone https://github.com/modelscope/agentscope.git
cd agentscope

# 2. 创建虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# 3. 安装开发模式 + 全部依赖
pip install -e ".[full]"
```

`-e` 表示 editable 模式——你修改了 `src/agentscope/` 下的代码，改动立刻生效。

### 验证安装

```python
import agentscope
print(agentscope.__version__)

from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.message import Msg
print("所有核心模块导入成功")
```

---

## 21.3 Step 2: 运行测试

```bash
# 运行所有测试
pytest tests/

# 运行单个测试文件
pytest tests/model_openai_test.py

# 按关键词筛选
pytest tests/ -k "memory"

# 隔离模式（每个测试独立进程）
pytest tests/ --forked
```

### 写一个简单的测试

```python
# test_my_first.py
from agentscope.message import Msg

def test_msg_creation():
    msg = Msg("user", "hello", "user")
    assert msg.name == "user"
    assert msg.content == "hello"
    assert msg.role == "user"
    assert msg.id is not None
    assert msg.timestamp is not None
```

```bash
pytest test_my_first.py -v
```

---

## 21.4 Step 3: 代码质量工具

```bash
# 安装 pre-commit
pip install pre-commit
pre-commit install

# 手动运行所有检查
pre-commit run --all-files
```

Pre-commit 会自动运行：
- `black`：代码格式化
- `flake8`：代码检查
- `mypy`：类型检查
- 其他项目自定义检查

### 类型检查

```bash
mypy src/agentscope/
```

### 代码检查

```bash
pylint src/agentscope/
flake8 src/agentscope/
```

---

## 21.5 Step 4: 项目结构导航

```
agentscope/
├── src/agentscope/     ← 源码（你要改的地方）
├── tests/              ← 测试
├── examples/           ← 示例代码
├── docs/               ← 文档
├── .github/            ← CI/CD 配置
├── pyproject.toml      ← 项目配置
└── CLAUDE.md           ← AI 辅助开发指引
```

### 快速定位技巧

```bash
# 找类定义
grep -rn "class ReActAgent" src/agentscope/

# 找方法
grep -rn "def format" src/agentscope/formatter/

# 找文件
find src/agentscope -name "*formatter*"
```

---

## 21.6 Step 5: 第一个源码修改

验证你能修改源码并看到效果：

1. 打开 `src/agentscope/message/_message_base.py`
2. 在 `Msg.__init__` 末尾加一行：

```python
self.invocation_id = invocation_id
print(f"[DEBUG] Msg created: name={self.name}, role={self.role}")  # 加这行
```

3. 运行测试：

```python
from agentscope.message import Msg
msg = Msg("user", "test", "user")
# 你会看到: [DEBUG] Msg created: name=user, role=user
```

4. 改完后**记得删掉这行**，保持代码干净

---

## 21.7 检查点

你现在已经准备好：

- 源码可编辑（`pip install -e`）
- 测试可运行（`pytest`）
- 代码质量检查可运行（`pre-commit`）
- 能修改源码并验证效果

---

## 下一章预告

环境搭好了。下一章，造一个新 Tool。
