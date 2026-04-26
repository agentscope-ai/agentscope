# 第六章：开发指南

> **版本提示**: 本章基于 AgentScope v1.0.19 编写。v2.0 正在开发中，将有 breaking changes。

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

```python
# tests/agent/test_react_agent.py

import pytest
from agentscope import agent
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
        return agent.ReActAgent(
            name="测试助手",
            model=mock_model
        )

    def test_basic_reply(self, agent):
        """测试基本回复"""
        response = agent("你好")
        assert "测试回复" in response

    def test_with_memory(self, mock_model):
        """测试记忆功能"""
        from agentscope.memory import InMemoryMemory

        memory = InMemoryMemory()
        test_agent = agent.ReActAgent(
            name="测试助手",
            model=mock_model,
            memory=memory
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
        return agent.DeepResearchAgent(
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

```python
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

```python
import agentscope
import logging

# 设置详细日志
logging.getLogger("agentscope").setLevel(logging.DEBUG)

# 打印模型输入输出
agent = agent.ReActAgent(
    name="助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
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

```python
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
| Agent 不调用工具 | 检查 tools 参数是否正确传递，工具是否有 `@function` 装饰器 |
| 记忆不生效 | 检查 memory 参数是否设置 |
| API 调用失败 | 检查 api_key 环境变量和网络 |
| 响应格式错误 | 检查 model 的 formatter 配置 |
| 推理死循环 | 检查 hook 是否重复执行，更新到 v1.0.19+ |
| 工具找不到 | 检查 `from agentscope.tool import xxx` 导入方式是否正确 |

## 6.6 新增多智能体模式

### Routing（路由模式）

根据输入内容自动路由到最合适的 Agent：

```python
from agentscope import agent, pipeline

router = pipeline.Routing(
    agents=[research_agent, writer_agent, coder_agent]
)

result = router("解释 Transformer 架构")  # 自动路由到 coder_agent
```

### Handoffs（交接模式）

Agent 之间可以交接对话：

```python
with pipeline.Handoffs(agents=[specialist_1, specialist_2]) as handoffs:
    result = specialist_1("复杂问题")
    # 自动交接给 specialist_2
```

### Supervisor（监督者模式）

监督者协调多个专家 Agent：

```python
with pipeline.Supervisor(agents=[researcher, writer]) as supervisor:
    article = supervisor("写一篇关于 AI 的研究报告")
```

### Sequential（顺序执行）

按顺序执行，前一个输出作为下一个输入：

```python
with pipeline.Sequential(agents=[researcher, writer]) as seq:
    research = researcher("研究 AI 趋势")
    article = writer(f"基于研究写文章: {research}")
```

### Debate（辩论模式）

多个 Agent 从不同角度分析问题：

```python
with pipeline.Debate(agents=[pro_agent, con_agent]) as debate:
    analysis = debate("这个商业决策是否明智？")
```

## 6.7 Voice Agent 开发

### 创建带语音输出的 Agent

```python
agent = agent.ReActAgent(
    name="语音助手",
    model=DashScopeModel(model_name="qwen-audio"),
    tools=[...],
    speech={
        "tts_api": "dashscope",  # 或 "openai", "german_tts"
        "voice": "female_2",
        "stream": True  # 流式输出
    }
)
```

### DeepResearchAgent 深度研究

```python
from agentscope.agent import DeepResearchAgent

research_agent = DeepResearchAgent(
    name="深度研究助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    tools=[tavily_search, tavily_extract, text_file],
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
