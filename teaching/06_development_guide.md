# 第六章：开发指南

> **版本提示**: 本章基于 AgentScope v1.0.19 编写。v2.0 正在开发中，将有 breaking changes。

## 学习目标

- 掌握 AgentScope 项目的开发环境搭建与依赖安装
- 理解代码规范（black、isort、flake8、mypy）与 pre-commit hooks 的使用
- 学会使用 pytest 编写和运行单元测试与集成测试
- 掌握 ReActAgent、DeepResearchAgent 的正确构造方式（sys_prompt、formatter、toolkit）
- 理解 SequentialPipeline、FanoutPipeline 的正确使用方式（直接实例化，非上下文管理器）
- 了解 Toolkit.register_tool_function() 注册工具的方法
- 了解 Voice Agent 开发与调试技巧

## 6.1 项目开发流程

```
┌─────────────────────────────────────────────────────────────┐
│                   AgentScope 开发流程                        │
│                                                             │
│  1. Clone 项目                                              │
│         │                                                   │
│         ▼                                                   │
│  2. 安装开发依赖                                             │
│     pip install -e ".[dev]"                                 │
│         │                                                   │
│         ▼                                                   │
│  3. 设置 pre-commit hooks (自动格式化/检查)                   │
│     pre-commit install                                       │
│         │                                                   │
│         ▼                                                   │
│  4. 开发 / 测试 / 提交                                       │
│     pytest tests/                                           │
│     git commit                                              │
│         │                                                   │
│         ▼                                                   │
│  5. 推送到远程                                               │
│     git push                                                │
└─────────────────────────────────────────────────────────────┘
```

## 6.2 环境配置

```bash
# 克隆项目
git clone https://github.com/agentscope-ai/agentscope.git
cd agentscope

# 安装开发依赖
pip install -e ".[dev]"

# 安装 pre-commit hooks
pre-commit install

# 验证安装
python -c "import agentscope; print(agentscope.__version__)"

> **注意**: v2.0 正在开发中，新项目建议使用 v1.0.19+ 以保证稳定性。
```

## 6.3 代码规范

### 格式化工具

| 工具 | 作用 | Java 类比 |
|------|------|-----------|
| `black` | 代码格式化 | `google-java-format` |
| `isort` | import 排序 | IDEA import optimizer |
| `flake8` | 风格检查 | `checkstyle` |
| `mypy` | 类型检查 | `Checker` |

### 运行检查

```bash
# 格式化代码
black src/ tests/
isort src/ tests/

# 静态检查
mypy src/agentscope/
flake8 src/agentscope/

# 全部检查
pre-commit run --all-files
```

### 代码规范要点

```python
# ✅ 正确: 类型注解
def process_message(self, msg: str) -> str:
    """处理消息"""
    return msg.strip()

# ✅ 正确: docstring
def calculate(a: int, b: int) -> int:
    """计算两个数的和

    Args:
        a: 第一个数
        b: 第二个数

    Returns:
        两数之和
    """
    return a + b

# ❌ 错误: 缺少类型注解
def process_message(msg):
    return msg.strip()
```

## 6.4 测试

### 测试框架

AgentScope 使用 `pytest` + `pytest-asyncio`：

```bash
# 运行所有测试
pytest tests/

# 运行特定测试文件
pytest tests/agent/test_react_agent.py

# 带覆盖率
pytest tests/ --cov=src/agentscope --cov-report=html

# 只运行单元测试 (跳过集成测试)
pytest tests/ -m "not integration"
```

### 测试结构

```
tests/
├── agent/
│   ├── test_react_agent.py
│   ├── test_user_agent.py
│   └── __init__.py
├── model/
│   ├── test_openai_model.py
│   └── __init__.py
├── tool/
│   ├── test_python_tool.py
│   └── __init__.py
├── memory/
│   ├── test_in_memory.py
│   └── __init__.py
└── conftest.py           # pytest fixtures
```

### 测试示例

```python showLineNumbers
# tests/agent/test_react_agent.py

import pytest
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit
from agentscope.model import MockModel  # 测试用 mock

class TestReActAgent:
    """测试 ReActAgent"""

    @pytest.fixture
    def mock_model(self):
        """Mock LLM 模型"""
        return MockModel(response="测试回复")


class MockModel:
    """测试用 Mock 模型，模拟 LLM 调用"""
    def __init__(self, response: str = "mock response"):
        self.response = response

    def __call__(self, *args, **kwargs):
        return self.response

    @pytest.fixture
    def agent(self, mock_model):
        """创建测试 Agent"""
        return ReActAgent(
            name="测试助手",
            model=mock_model,
            sys_prompt="你是一个测试助手。",
            formatter=OpenAIChatFormatter(),
            toolkit=Toolkit(),
        )

    def test_basic_reply(self, agent):
        """测试基本回复"""
        response = agent("你好")
        assert "测试回复" in response

    def test_with_memory(self, mock_model):
        """测试记忆功能"""
        from agentscope.memory import InMemoryMemory

        memory = InMemoryMemory()
        test_agent = ReActAgent(
            name="测试助手",
            model=mock_model,
            memory=memory,
            sys_prompt="你是一个测试助手。",
            formatter=OpenAIChatFormatter(),
            toolkit=Toolkit(),
        )

        test_agent("我叫张三")
        test_agent("我叫什么名字？")

        # 验证记忆被记录
        assert len(memory.get_history()) > 0


class TestDeepResearchAgent:
    """测试 DeepResearchAgent"""

    @pytest.fixture
    def mock_model(self):
        return MockModel(response="研究结果")

    @pytest.fixture
    def research_agent(self, mock_model):
        """创建深度研究 Agent"""
        from agentscope.agent import DeepResearchAgent
        return DeepResearchAgent(
            name="研究助手",
            model=mock_model,
            max_depth=2
        )

    def test_research(self, research_agent):
        """测试研究功能"""
        result = research_agent("AI 发展趋势")
        assert result is not None
```

### Java 对比

```java
// Java: JUnit 5
class ReActAgentTest {
    @Mock
    private Model mockModel;

    @InjectMocks
    private ReActAgent agent;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
    }

    @Test
    void testBasicReply() {
        when(mockModel.invoke(any())).thenReturn("测试回复");

        String response = agent.reply("你好");

        assertTrue(response.contains("测试回复"));
    }
}
```

## 6.5 调试技巧

### 启用调试模式

```python showLineNumbers
import agentscope

# 启用调试模式 (详细日志)
agentscope.init(
    project="my-project",
    debug=True
)

# 或者设置环境变量
# export AGENTSCOPE_DEBUG=true
```

> **API 提示**: `agentscope.init()` 参数已更新：
> - `project_name` → `project`
> - `api_key` 参数已移除（通过环境变量设置）

### 查看 LLM 调用

```python showLineNumbers
import agentscope
import logging

# 设置详细日志
logging.getLogger("agentscope").setLevel(logging.DEBUG)

# 打印模型输入输出
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit

agent = ReActAgent(
    name="助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个有帮助的助手。",
    formatter=OpenAIChatFormatter(),
    toolkit=Toolkit(),
)

# 添加回调查看调用
def print_callback(step_info):
    print(f"Step: {step_info['step']}")
    print(f"Thought: {step_info.get('thought', 'N/A')}")
    print(f"Action: {step_info.get('action', 'N/A')}")
    print(f"Observation: {step_info.get('observation', 'N/A')}")

# Agent 会打印每个推理步骤
response = agent("你的问题", callbacks=[print_callback])
```

### AgentScope Studio 可视化调试

```python showLineNumbers
# 启用 Studio 进行可视化调试
agentscope.init(
    project="my-project",
    studio_url="http://localhost:5000",  # Studio 服务地址
    debug=True
)
```

Studio 提供：
- 消息时间线视图和聚合回复视图
- ReAct agent 中间状态可视化
- OpenTelemetry 全链路追踪

### 常见问题排查

| 问题 | 排查方法 |
|------|----------|
| Agent 不调用工具 | 检查 toolkit 是否正确传递，工具是否通过 `Toolkit.register_tool_function()` 注册 |
| 记忆不生效 | 检查 memory 参数是否设置 |
| API 调用失败 | 检查 api_key 环境变量和网络 |
| 响应格式错误 | 检查 model 的 formatter 配置 |
| 推理死循环 | 检查 hook 是否重复执行，更新到 v1.0.19+ |
| 工具找不到 | 检查 `from agentscope.tool import xxx` 导入方式是否正确 |

## 6.6 多智能体模式

### SequentialPipeline（顺序执行）

按顺序执行，前一个输出作为下一个输入：

```python showLineNumbers
from agentscope.pipeline import SequentialPipeline

seq = SequentialPipeline(agents=[researcher, writer])
research = researcher("研究 AI 趋势")
article = writer(f"基于研究写文章: {research}")
```

### FanoutPipeline（广播模式）

将任务广播给多个 Agent 并收集响应：

```python showLineNumbers
from agentscope.pipeline import FanoutPipeline

fanout = FanoutPipeline(agents=[agent1, agent2, agent3])
results = fanout("并行执行这个任务")
```

### ChatRoom（聊天室模式）

支持多 Agent 之间的自由消息传递：

```python showLineNumbers
from agentscope.pipeline import ChatRoom
import asyncio

room = ChatRoom(agents=[agent1, agent2, agent3])

# 需要通过队列发送消息
async def send_messages():
    outgoing_queue = Queue()
    await room.start(outgoing_queue)
    # 消息通过 agent 的 speak() 方法发送
    # room 会在后台自动广播消息
    await asyncio.sleep(10)  # 保持连接
    await room.stop()
```

### MsgHub（消息中心）

作为代理间的消息中介，支持订阅-发布模式：

```python showLineNumbers
from agentscope.pipeline import MsgHub

async with MsgHub(participants=[agent1, agent2, agent3]) as hub:
    # agent 的回复会自动广播给其他参与者
    result = await agent1("开始协作")
```

## 6.7 Voice Agent 开发

### 创建带语音输出的 Agent

```python showLineNumbers
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.tool import Toolkit

agent = ReActAgent(
    name="语音助手",
    model=DashScopeChatModel(model_name="qwen-audio"),
    sys_prompt="你是一个语音助手。",
    formatter=DashScopeChatFormatter(),
    toolkit=Toolkit(),
    speech={
        "tts_api": "dashscope",  # 或 "openai", "german_tts"
        "voice": "female_2",
        "stream": True  # 流式输出
    }
)
```

### DeepResearchAgent 深度研究

```python showLineNumbers
from agentscope.agent import DeepResearchAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit

# 注意：DeepResearchAgent 是一个高级模式示例，非核心库类
# 它展示了 ReActAgent 的深度研究扩展
toolkit = Toolkit()
toolkit.register_tool_function(tavily_search)
toolkit.register_tool_function(tavily_extract)
toolkit.register_tool_function(text_file)

research_agent = DeepResearchAgent(
    name="深度研究助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    sys_prompt="你是一个深度研究助手，负责进行全面的信息收集和分析。",
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,
    max_depth=3,
    max_tokens=100000
)

report = research_agent("研究 AI 在医疗领域的应用")
```

> **v1.0.19 修复**: DeepResearchAgent 的 memory.add 已修复，now supports thinking 和 non-thinking 模型。

```bash
# 列出所有示例
ls examples/

# 运行单个示例
cd examples/agent/react_agent/
python main.py

# 运行工作流示例
cd examples/workflows/multiagent_conversation/
python main.py
```

## 6.8 Git 提交规范

AgentScope 使用 conventional commits：

```bash
# 提交格式
git commit -m "type(scope): description"

# 示例
git commit -m "feat(agent): add new ReActAgent implementation"
git commit -m "fix(model): handle timeout error in OpenAI API"
git commit -m "docs(readme): update installation guide"
git commit -m "test(memory): add unit tests for RedisMemory"
```

**Type 列表：**

| Type | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档更新 |
| `style` | 代码格式（不影响功能） |
| `refactor` | 重构 |
| `test` | 测试相关 |
| `chore` | 构建/工具变更 |

## 6.9 下一步

- [第七章：Java 开发者视角](07_java_comparison.md) - 对比学习，加深理解

---

## 本章总结

- **环境配置**: 通过 `pip install -e ".[dev]"` 安装开发依赖，使用 `pre-commit install` 设置自动检查
- **代码规范**: 使用 black（格式化）、isort（import 排序）、flake8（风格检查）、mypy（类型检查）
- **测试**: 基于 pytest + pytest-asyncio，通过 `pytest tests/` 运行测试，支持覆盖率报告
- **ReActAgent 构造**: 必须提供 `sys_prompt`、`formatter`、`toolkit`（非 `tools`）三个核心参数
- **工具注册**: 使用 `Toolkit.register_tool_function()` 注册工具函数，而非 `@function` 装饰器
- **Pipeline 使用**: SequentialPipeline 和 FanoutPipeline 通过直接实例化使用，不支持上下文管理器
- **调试**: 通过 `agentscope.init(debug=True)` 启用调试模式，AgentScope Studio 提供可视化调试
- **Voice Agent**: ReActAgent 通过 `speech` 参数支持 TTS 语音输出

## 练习题

### 练习 6.1: 代码规范检查 [基础]

**题目**：
以下 Python 代码违反了 AgentScope 的代码规范，请指出问题：

```python showLineNumbers
# test_agent.py
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel, DashScopeChatModel
from agentscope.formatter import OpenAIChatFormatter

def create_agent():
    agent = ReActAgent(
        name="助手",
        model=OpenAIChatModel(model_name="gpt-4o"),
        sys_prompt="你是一个有帮助的助手。",
        formatter=OpenAIChatFormatter(),
    )
    return agent
```

**验证方式**：
使用 `flake8`、`isort`、`black` 检查代码。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**问题 1：import 顺序**
- `DashScopeChatModel` 被导入但未使用
- 应使用 `isort` 排序：标准库 → 第三方库 → 本地库

**问题 2：缺少类型注解**
- `create_agent` 函数应有返回类型注解

**问题 3：函数之间应有空行**
- PEP 8 规定函数之间应有两个空行

**修正后的代码**：
```python showLineNumbers
# test_agent.py
import agentscope
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.model import OpenAIChatModel


def create_agent() -> ReActAgent:
    """创建测试用 Agent"""
    return ReActAgent(
        name="助手",
        model=OpenAIChatModel(model_name="gpt-4o"),
        sys_prompt="你是一个有帮助的助手。",
        formatter=OpenAIChatFormatter(),
    )
```

**运行检查命令**：
```bash
isort test_agent.py  # 自动排序 imports
black test_agent.py  # 格式化代码
flake8 test_agent.py  # 风格检查
```
</details>

---

### 练习 6.2: 测试用例编写 [中级]

**题目**：
请为以下工具函数编写一个 pytest 测试用例：

```python showLineNumbers
def add_numbers(a: int, b: int) -> ToolResponse:
    """计算两个数的和

    Args:
        a: 第一个数
        b: 第二个数
    """
    result = a + b
    return ToolResponse(content=[TextBlock(type="text", text=str(result))])
```

**验证方式**：
检查测试用例是否正确覆盖正常和边界情况。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**测试用例示例**：

```python showLineNumbers
# tests/tool/test_my_tools.py
import pytest
from agentscope.tool import ToolResponse
from agentscope.message import TextBlock
from my_module import add_numbers  # 假设函数在 my_module 中


class TestAddNumbers:
    """测试 add_numbers 函数"""

    def test_positive_numbers(self):
        """测试正数相加"""
        result = add_numbers(1, 2)
        assert isinstance(result, ToolResponse)
        assert "3" in result.content[0].text

    def test_negative_numbers(self):
        """测试负数相加"""
        result = add_numbers(-5, -3)
        assert "-8" in result.content[0].text

    def test_mixed_numbers(self):
        """测试正负数混合"""
        result = add_numbers(10, -3)
        assert "7" in result.content[0].text

    def test_zero(self):
        """测试零"""
        result = add_numbers(0, 5)
        assert "5" in result.content[0].text

    def test_large_numbers(self):
        """测试大数"""
        result = add_numbers(999999, 1)
        assert "1000000" in result.content[0].text
```

**pytest 常用命令**：
```bash
pytest tests/tool/test_my_tools.py -v          # 详细输出
pytest tests/tool/test_my_tools.py --cov=my_module  # 覆盖率
```
</details>

---

### 练习 6.3: 调试模式启用 [基础]

**题目**：
小王发现 Agent 的响应不符合预期，想要启用调试模式来排查问题。请帮他修改以下代码：

```python showLineNumbers
import agentscope

agentscope.init(
    project="my-project",
    logging_level="INFO"  # 当前是 INFO，想改成 DEBUG
)
```

**验证方式**：
检查是否正确启用了调试模式。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**方案 1：修改 logging_level**

```python showLineNumbers
import agentscope

agentscope.init(
    project="my-project",
    logging_level="DEBUG"  # 改为 DEBUG 级别
)
```

**方案 2：设置 debug=True**

```python showLineNumbers
import agentscope

agentscope.init(
    project="my-project",
    debug=True  # 启用调试模式
)
```

**debug=True 和 logging_level="DEBUG" 的区别**：

| 参数 | 作用范围 | 说明 |
|------|----------|------|
| `debug=True` | 框架级别 | 启用详细的框架日志，包括内部状态 |
| `logging_level="DEBUG"` | 日志级别 | 仅改变日志输出的详细程度 |

**推荐使用 `debug=True`**，因为它还会启用其他调试相关的功能。

**调试时查看的具体信息**：
- LLM 调用的输入输出
- 工具调用的参数和结果
- 记忆的读写操作
- Hook 的执行情况
</details>

---

### 练习 6.4: Mock 测试 [挑战]

**题目**：
某团队需要测试一个依赖外部 API 的 Agent，但不想每次都调用真实 API。请使用 unittest.mock 为以下场景编写 Mock 测试：

**场景**：测试当 LLM 返回工具调用时，Agent 是否正确执行工具。

**验证方式**：
检查 Mock 设置是否正确，测试用例是否能验证工具调用逻辑。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**Mock 测试示例**：

```python showLineNumbers
# tests/agent/test_react_agent_tool_call.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ToolUseBlock
from agentscope.model import ChatModelBase, ChatResponse


class MockModelWithToolCall(ChatModelBase):
    """模拟返回工具调用的 Model"""

    def __init__(self):
        super().__init__()
        self.model_name = "mock-model"
        self.stream = False

    async def __call__(self, *args, **kwargs):
        """返回 ToolUseBlock，模拟 LLM 决定调用工具"""
        return ChatResponse(
            content=[
                TextBlock(type="text", text="我将为你计算"),
                ToolUseBlock(
                    type="tool_use",
                    id="call_123",
                    name="calculate",
                    input={"a": 10, "b": 20},
                ),
            ],
            id="mock-123",
            created_at="2024-01-01",
            type="chat",
            usage=None,
            metadata=None,
        )


@pytest.fixture
def mock_model():
    return MockModelWithToolCall()


@pytest.fixture
def agent(mock_model):
    return ReActAgent(
        name="测试助手",
        model=mock_model,
        sys_prompt="你是一个计算助手。",
        formatter=OpenAIChatFormatter(),
        toolkit=Toolkit(),
    )


@pytest.mark.asyncio
async def test_agent_calls_tool(agent):
    """测试 Agent 正确调用工具"""
    # 调用 Agent
    response = await agent("计算 10 + 20")

    # 验证：Agent 应该执行了工具
    # （具体断言取决于实现，可能需要检查记忆或工具调用记录）
    assert response is not None
    assert isinstance(response, Msg)
```

**Mock 测试的关键点**：
1. 创建继承自 `ChatModelBase` 的 Mock 类
2. 在 `__call__` 中返回模拟的 `ChatResponse`
3. 使用 `pytest.mark.asyncio` 标记异步测试
4. 使用 `@pytest.fixture` 管理测试依赖
</details>

---

### 练习 6.5: pre-commit 配置 [基础]

**题目**：
某新同事克隆了 AgentScope 仓库，但提交代码时 pre-commit 检查失败了。请说明：
1. 如何安装 pre-commit hooks？
2. pre-commit 会在什么时机自动运行？
3. 如果想跳过 pre-commit 临时提交代码，应该怎么做（不推荐）？

**验证方式**：
对照文档中的 pre-commit 使用说明。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**1. 安装 pre-commit hooks**：

```bash
# 在项目根目录下执行
pre-commit install
```

**2. 自动运行时机**：

`pre-commit` 会在以下时机自动运行：
- **git commit 时**：在提交之前自动执行所有 hooks
- **git push 时**：如果配置了 `git push` 时运行
- **手动触发**：`pre-commit run --all-files`

**pre-commit 工作流程**：
```
git commit → pre-commit hook 触发 → 检查代码 → 通过则提交，失败则拒绝
```

**3. 跳过 pre-commit（不推荐）**：

```bash
# 临时跳过 pre-commit
git commit --no-verify -m "临时提交"
```

**警告**：不推荐跳过 pre-commit，因为：
- 代码规范检查失败说明代码有问题
- 跳过后可能导致 CI/CD 失败
- 影响团队代码质量

**推荐做法**：
1. 先运行 `pre-commit run --all-files` 查看具体问题
2. 修复问题后再次提交
3. 保持代码符合规范

**常见 pre-commit 检查**：
| 检查工具 | 作用 |
|----------|------|
| `black` | 代码格式化 |
| `isort` | import 排序 |
| `flake8` | 代码风格 |
| `mypy` | 类型检查 |
</details>

## 6.9 下一步
