# AgentScope 最佳实践参考手册

> 本文档收集 AgentScope 智能体开发的设计模式、Prompt Engineering 最佳实践、生产部署经验、RAG 优化策略、安全性最佳实践和测试策略。所有内容均来自实际访问的资料，并标注了原始来源链接。

## 学习目标

- 掌握 ReAct、Plan-and-Execute 等核心智能体设计模式及其在 AgentScope 中的实现
- 学会编写高质量的 Prompt：结构化输出、Few-Shot、思维链、角色扮演
- 理解工具调用优化策略：单一职责、并行调用、错误处理
- 了解 RAG 优化要点：分块策略、嵌入模型选择、混合检索
- 建立生产部署意识：性能优化、监控、安全防护、测试金字塔

---

## 一、Agent 设计模式

### 1.1 ReAct 模式

#### 1.1.1 模式概述

ReAct（Reasoning and Acting）是最常用的 LLM 智能体范式，将推理和行动交替进行。核心思想是模拟人类的问题解决过程：

1. **思考（Thought）**：分析当前情况，决定下一步行动
2. **行动（Action）**：执行工具或API调用
3. **观察（Observation）**：获取行动结果，更新上下文
4. **重复**：直到任务完成

#### 1.1.2 提示词模板

```python
REACT_PROMPT = """
Answer the following questions as best you can. You have access to the following tools:

{tools}

Use the following format:

Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input for the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)

Thought: I now know the final answer
Final Answer: the final answer to the original input question

Question: {input}
Thought: {agent_scratchpad}
"""
```

#### 1.1.3 AgentScope 实现

```python
from agentscope.agent import ReActAgent

agent = ReActAgent(
    name="assistant",
    sys_prompt="你是一个有用的助手",
    model=model,
    formatter=formatter,
    toolkit=toolkit,
)
```

#### 1.1.4 适用场景

| 场景 | 推荐程度 | 说明 |
|------|---------|------|
| 简单直接任务 | ✅ 强烈推荐 | 步骤少、路径清晰 |
| 实时交互场景 | ✅ 强烈推荐 | 可快速响应用户输入 |
| 成本敏感场景 | ✅ 强烈推荐 | 迭代次数可控 |
| 复杂多步骤任务 | ⚠️ 慎用 | 规划能力有限 |
| 需要全局规划 | ⚠️ 慎用 | 可能陷入局部最优 |

### 1.2 Plan-and-Execute 模式

#### 1.2.1 模式概述

Plan-and-Execute 采用"先规划，后执行"的策略，将任务分为两个阶段：

1. **规划阶段**：LLM 生成完整的任务计划
2. **执行阶段**：按计划逐步执行任务

#### 1.2.2 提示词模板

```python
PLANNER_PROMPT = """
You are a task planning assistant. Given a task, create a detailed plan.

Task: {input}

Create a plan with the following format:
1. First step
2. Second step
...

Plan:"""

EXECUTOR_PROMPT = """
You are a task executor. Follow the plan and execute each step using available tools:

{tools}

Plan:
{plan}

Current step: {current_step}
Previous results: {previous_results}

Use the following format:

Thought: think about the current step
Action: the action to take
Action Input: the input for the action"""
```

#### 1.2.3 适用场景

| 场景 | 推荐程度 | 说明 |
|------|---------|------|
| 复杂多步骤任务 | ✅ 强烈推荐 | 全局视野避免错误累积 |
| 高准确性场景 | ✅ 强烈推荐 | 可在执行前人工审核计划 |
| 长时规划任务 | ✅ 强烈推荐 | 计划可追溯、可修改 |
| 简单直接任务 | ⚠️ 慎用 | 额外规划开销不划算 |
| 动态调整需求 | ⚠️ 慎用 | 计划修改成本高 |

### 1.3 多智能体协作模式

#### 1.3.1 模式类型

| 模式 | 描述 | AgentScope 支持 |
|------|------|----------------|
| 管道模式 | 线性任务流水线 | ✅ MsgHub |
| 路由模式 | 根据输入类型分发 | ✅ 内置 |
| 监督者模式 | 中央协调者调度专家 | ✅ Agent as Tool |
| 辩论模式 | 多智能体辩论决策 | ✅ Multi-Agent Debate |
| 技能模式 | 按需加载领域技能 | ✅ Skills |

#### 1.3.2 AgentScope MsgHub

MsgHub 是 AgentScope 的消息中枢，实现智能体间通信路由：

```python
from agentscope.pipeline import MsgHub, SequentialPipeline

# 创建消息中心
async with MsgHub(participants=[agent1, agent2, agent3]) as hub:
    # 使用 SequentialPipeline 顺序执行
    await SequentialPipeline(agents=[agent1, agent2, agent3])
```

#### 1.3.3 多智能体辩论

```python
# AgentScope 中的多智能体辩论
from agentscope.agent import ReActAgent
from agentscope.message import Msg

pro_agent = ReActAgent(name="正方", ...)
con_agent = ReActAgent(name="反方", ...)

# 辩论流程
for round in range(3):
    pro_msg = Msg(name="user", content=debate_topic, role="user")
    pro_response = await pro_agent(pro_msg)
    con_msg = Msg(name="user", content=f"正方观点：{pro_response.content}", role="user")
    con_response = await con_agent(con_msg)
```

### 1.4 混合使用策略

#### 1.4.1 何时混合使用

- **ReAct + Plan-and-Execute**：复杂任务用 Plan-and-Execute，子任务用 ReAct
- **单智能体 + 多智能体**：简单任务用单智能体，复杂任务启动多智能体协作

#### 1.4.2 实现示例

```python
class HybridAgent:
    def __init__(self):
        self.planner = ReActAgent(name="planner", ...)
        self.executors = [
            ReActAgent(name="executor_1", ...),
            ReActAgent(name="executor_2", ...),
        ]

    async def run(self, task):
        # 规划阶段
        plan = await self.planner.run(f"制定计划：{task}")

        # 执行阶段
        results = []
        for step in plan.steps:
            executor = self.select_executor(step)
            result = await executor.run(step)
            results.append(result)

        return self.synthesize(results)
```

---

## 二、Prompt Engineering 最佳实践

### 2.1 基本原则

#### 2.1.1 清晰明确

```
❌ 不好：回答关于公司的问题

✅ 更好：作为公司知识库助手，回答关于产品功能、定价和服务的问题。
如果问题超出知识范围，说"我目前没有这方面的信息"。
```

#### 2.1.2 结构化输出

```python
# 使用 Pydantic 模型定义输出格式
from pydantic import BaseModel, Field

class CustomerResponse(BaseModel):
    sentiment: str = Field(description="情感：positive/negative/neutral")
    key_points: list[str] = Field(description="关键点列表")
    action_items: list[str] = Field(description="需要采取的行动")
    confidence: float = Field(description="置信度 0-1")
```

#### 2.1.3 约束条件

```
✅ 添加明确的约束：
- 不要编造信息，只使用提供的上下文
- 如果信息不足，明确说明"我不知道"
- 回答要简洁，不超过 3 句话
```

### 2.2 高级技巧

#### 2.2.1 Few-Shot 示例

```python
SYSTEM_PROMPT = """
你是一个客服助手。用户抱怨产品质量问题时，按以下格式回复：

示例 1：
用户：这个东西两天就坏了
助手：非常抱歉给您带来不便。您可以提供购买凭证和照片吗？

示例 2：
用户：收到的东西和描述不符
助手：抱歉造成困扰。请问您需要退款还是换货？
"""
```

#### 2.2.2 思维链（Chain of Thought）

```
在回答复杂问题时，请按以下步骤思考：

1. 问题分析：这个问题涉及哪些方面？
2. 信息收集：我需要哪些信息来回答？
3. 推理过程：基于已有信息，如何得出结论？
4. 最终回答：给出清晰、准确的答案
```

#### 2.2.3 角色扮演

```
✅ 你是一个[角色]，有[years]年经验，專精於[领域]。
你的目标是[目标]。
沟通风格：[正式/友好/专业]
```

### 2.3 AgentScope 提示词配置

#### 2.3.1 系统提示词

```python
agent = ReActAgent(
    name="assistant",
    sys_prompt="""你是一个专业的法律顾问助手。
    - 只提供信息，不能替代律师咨询
    - 涉及具体法律建议时，说明"请咨询专业律师"
    - 使用清晰的法律术语解释概念""",
    model=model,
    toolkit=toolkit,
)
```

#### 2.3.2 自定义格式化器

```python
from agentscope.formatter import FormatterBase

class CustomFormatter(FormatterBase):
    def format(self, messages: list[Msg]) -> list[dict]:
        # 自定义格式化逻辑
        formatted = []
        for msg in messages:
            formatted.append({
                "role": msg.role,
                "content": self.process_content(msg.content),
                "name": msg.name
            })
        return formatted
```

---

## 三、工具调用优化

### 3.1 工具设计原则

#### 3.1.1 单一职责

```
❌ 不好：get_weather_and_news(location)  # 多个功能

✅ 更好：
- get_weather(location)  # 只获取天气
- get_news(location)     # 只获取新闻
```

#### 3.1.2 清晰的参数描述

```python
from pydantic import Field
from agentscope.tool import Toolkit

toolkit = Toolkit()

def search_flights(
    origin: str = Field(description="出发城市，格式：城市名（机场代码）"),
    destination: str = Field(description="目的城市，格式：城市名（机场代码）"),
    date: str = Field(description="出发日期，格式：YYYY-MM-DD"),
    passengers: int = Field(description="乘客数量，默认1")
) -> str:
    """搜索航班信息"""
    ...

toolkit.register_tool_function(search_flights)
```

### 3.2 工具调用策略

#### 3.2.1 并行工具调用

```python
# AgentScope 并行工具调用
agent = ReActAgent(
    name="assistant",
    model=model,
    toolkit=toolkit,
    parallel_tool_calls=True,  # 启用并行
)

# 使用 generate_kwargs 配置
model = DashScopeChatModel(
    model_name="qwen-plus",
    api_key=api_key,
    generate_kwargs={"parallel_tool_calls": True},
)
```

#### 3.2.2 工具选择策略

```python
# 自动选择
tool_choice = "auto"  # 模型自行决定

# 强制调用特定工具
tool_choice = {"type": "function", "function": {"name": "get_weather"}}

# 禁用工具调用
tool_choice = "none"
```

### 3.3 工具错误处理

```python
from agentscope.tool import Toolkit

toolkit = Toolkit()

def unreliable_tool(query: str) -> str:
    """可能失败的工具"""
    try:
        # 执行操作
        return f"结果：{result}"
    except APIError as e:
        # 返回友好错误消息
        return f"服务暂时不可用，请稍后重试。错误：{e}"
    except RateLimitError:
        return "请求过于频繁，请等待后重试"

toolkit.register_tool_function(unreliable_tool)
```

---

## 四、RAG 优化策略

### 4.1 检索优化

#### 4.1.1 分块策略

| 策略 | 适用场景 | 块大小 |
|------|---------|--------|
| 固定大小 | 通用场景 | 512-1024 tokens |
| 句子级 | 精确匹配需求 | 单句 |
| 段落级 | 保留上下文 | 段落 |
| 语义级 | 主题完整 | 语义单元 |

#### 4.1.2 嵌入模型选择

```python
from agentscope.embedding import OpenAITextEmbedding

# 选择合适的嵌入模型
embedding_model = OpenAITextEmbedding(
    model_name="text-embedding-3-small",
)
```

#### 4.1.3 混合检索

```python
# 结合向量检索和关键词检索
query_embedding = embedding_model.encode(query)
keyword_results = keyword_search(query)

# 合并结果
hybrid_results = fusion(
    vector_results,
    keyword_results,
    weight=0.7  # 向量权重
)
```

### 4.2 生成优化

#### 4.2.1 上下文压缩

```python
# 只保留最相关的上下文
def compress_context(context: list, max_tokens: int) -> list:
    """压缩上下文至指定长度"""
    compressed = []
    current_tokens = 0

    for doc in sorted_by_relevance(context):
        doc_tokens = count_tokens(doc)
        if current_tokens + doc_tokens <= max_tokens:
            compressed.append(doc)
            current_tokens += doc_tokens
        else:
            break

    return compressed
```

#### 4.2.2 引用标注

```
✅ 在回答中标注来源：

根据[文档A]的说明，...
此外，[文档B]指出...
```

### 4.3 评估指标

| 指标 | 描述 | 目标值 |
|------|------|--------|
| 上下文相关性 | 检索到的文档与问题的相关程度 | > 0.8 |
| 答案忠诚度 | 答案与检索上下文的一致性 | > 0.9 |
| 答案相关性 | 答案对问题的帮助程度 | > 0.85 |

---

## 五、生产部署经验

### 5.1 部署架构

> **重要说明**：`agentscope-runtime` 是与核心 `agentscope` 框架分离的独立包，提供运行时和部署功能。
> 详情见：https://github.com/agentscope-ai/agentscope-runtime

#### 5.1.1 本地部署（核心框架）

```bash
# 安装 AgentScope 核心框架
pip install agentscope[full]

# 运行 Agent 脚本
python your_agent.py
```

#### 5.1.2 本地部署（agentscope-runtime 包）

```bash
# 安装运行时包（独立于核心框架）
pip install agentscope-runtime

# 启动 Studio 控制台
agentscope studio
# 访问 http://localhost:8000 进入控制台
```

#### 5.1.3 Docker 部署

```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ./agentscope ./agentscope

# 安装运行时依赖
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 运行 Agent（使用核心框架）
CMD ["python", "your_agent.py"]

# 或者使用 agentscope-runtime 运行时
# CMD ["agentscope", "run", "your_agent.py"]
```

#### 5.1.3 Kubernetes 部署

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentscope-agent
spec:
  replicas: 3
  selector:
    matchLabels:
      app: agentscope-agent
  template:
    spec:
      containers:
      - name: agent
        image: agentscope:latest
        ports:
        - containerPort: 8000
        env:
        - name: DASHSCOPE_API_KEY
          valueFrom:
            secretKeyRef:
              name: api-keys
              key: dashscope
```

### 5.2 性能优化

#### 5.2.1 缓存策略

```python
from functools import lru_cache

@lru_cache(maxsize=1000)
def cached_embedding(text: str) -> list[float]:
    """缓存嵌入结果"""
    return embedding_model.encode(text)
```

#### 5.2.2 批量处理

```python
async def batch_process(queries: list[str]) -> list[str]:
    """批量处理查询"""
    from agentscope.message import Msg
    # 合并相似请求
    msg_list = [Msg(name="user", content=q, role="user") for q in queries]
    results = await asyncio.gather(*[agent(msg) for msg in msg_list])
    return [r.content for r in results]
```

#### 5.2.3 异步优化

```python
async def async_agent_workflow():
    from agentscope.message import Msg
    # 并行执行独立任务
    weather_task = weather_agent(Msg(name="user", content="杭州天气", role="user"))
    news_task = news_agent(Msg(name="user", content="今日新闻", role="user"))

    weather, news = await asyncio.gather(weather_task, news_task)

    # 汇总结果
    summary = await summary_agent(Msg(
        name="user",
        content=f"天气：{weather.content}，新闻：{news.content}",
        role="user"
    ))
    return summary.content
```

### 5.3 监控与日志

#### 5.3.1 OpenTelemetry 集成

```python
from agentscope.tracing import setup_tracing

# 配置追踪
setup_tracing(
    service_name="agentscope-agent",
    exporter="otlp",
    endpoint="http://otel-collector:4317"
)
```

#### 5.3.2 关键指标

| 指标 | 描述 | 告警阈值 |
|------|------|---------|
| 请求延迟 P99 | 99% 请求的响应时间 | > 5s |
| 错误率 | 失败请求比例 | > 1% |
| 工具调用成功率 | 工具调用成功比例 | < 99% |
| Token 消耗 | 每分钟消耗的 token 数 | > 阈值 |

---

## 六、安全性最佳实践

### 6.1 威胁模型

#### 6.1.1 OWASP LLM Top 10 (2025)

| 风险 ID | 类别 | 严重性 |
|--------|------|--------|
| LLM01 | 提示词注入 | 严重 |
| LLM02 | 敏感信息泄露 | 高 |
| LLM03 | 供应链漏洞 | 高 |
| LLM05 | 输出处理不当 | 高 |
| LLM06 | 过度授权 | 高 |
| LLM07 | 系统提示词泄露 | 中 |
| LLM08 | 向量和嵌入弱点 | 中 |
| LLM10 | 无界消耗 | 中 |

#### 6.1.2 Agentic Top 10 (2026) 新增

| 风险 ID | 类别 | 描述 |
|--------|------|------|
| ASI01 | 智能体目标劫持 | 攻击者通过中毒输入操纵自主智能体 |

### 6.2 安全实践

#### 6.2.1 输入验证

```python
from pydantic import BaseModel, validator

class UserQuery(BaseModel):
    query: str
    user_id: str

    @validator('query')
    def validate_query(cls, v):
        # 长度限制
        if len(v) > 1000:
            raise ValueError("查询过长")
        # 危险模式检测
        dangerous_patterns = ["ignore previous", "disregard instructions"]
        for pattern in dangerous_patterns:
            if pattern.lower() in v.lower():
                raise ValueError("无效查询")
        return v
```

#### 6.2.2 工具权限控制

```python
# 定义工具权限
TOOL_PERMISSIONS = {
    "get_weather": ["read"],
    "send_email": ["write", "user:verified"],
    "execute_code": ["admin"],
    "delete_data": ["admin", "audit"],
}

def check_permission(tool_name: str, user_role: str) -> bool:
    """检查用户是否有权使用工具"""
    required = TOOL_PERMISSIONS.get(tool_name, [])
    return any(role in required for role in user_role.split(":"))
```

#### 6.2.3 输出过滤

```python
def filter_output(response: str) -> str:
    """过滤敏感输出"""
    # 移除敏感信息模式
    patterns = [
        r'\d{16}'  # 信用卡号
        r'password[:\s]+\S+'  # 密码
        r'api[_-]?key[:\s]+\S+'  # API密钥
    ]

    for pattern in patterns:
        response = re.sub(pattern, '[REDACTED]', response)

    return response
```

### 6.3 生产安全检查清单

```
✅ 身份与认证
- [ ] 实现智能体身份验证（如 DID）
- [ ] 使用 OAuth 2.0 进行 API 访问
- [ ] 定期轮换密钥

✅ 授权与边界
- [ ] 定义工具使用权限
- [ ] 限制单次操作的影响范围
- [ ] 实现操作审计日志

✅ 验证层
- [ ] 添加输入验证和清理
- [ ] 实现输出过滤
- [ ] 使用结构化输出减少注入风险

✅ 撤销与熔断
- [ ] 实现操作撤销机制
- [ ] 设置熔断器防止级联失败
- [ ] 提供人工介入接口

✅ 可观测性与审计
- [ ] 记录所有操作日志
- [ ] 实现实时监控告警
- [ ] 定期安全审计
```

---

## 七、测试策略

### 7.1 测试金字塔

```
        /\
       /  \
      / E2E\     ← 5% 端到端测试
     /------\
    /Integration\ ← 25% 集成测试
   /------------\
  /   Unit Tests  \ ← 70% 单元测试
 /----------------\
```

### 7.2 单元测试

#### 7.2.1 工具测试

```python
import pytest
from agentscope.tool import Toolkit

def add_numbers(a: int, b: int) -> int:
    """加法工具"""
    return a + b

def test_add_numbers_basic():
    """测试基本功能"""
    result = add_numbers(1, 2)
    assert result == 3

def test_add_numbers_negative():
    """测试负数"""
    result = add_numbers(-1, 1)
    assert result == 0

def test_add_numbers_zero():
    """测试零"""
    result = add_numbers(0, 0)
    assert result == 0
```

#### 7.2.2 智能体组件测试

```python
@pytest.mark.asyncio
async def test_agent_response_format():
    """测试智能体响应格式"""
    from agentscope.message import Msg
    agent = ReActAgent(
        name="test",
        model=mock_model,
        toolkit=toolkit
    )

    response = await agent(Msg(name="user", content="测试输入", role="user"))

    # 验证响应结构
    assert hasattr(response, 'content')
    assert isinstance(response.content, str)
    assert len(response.content) > 0
```

### 7.3 集成测试

#### 7.3.1 工作流测试

```python
@pytest.mark.asyncio
async def test_multi_agent_workflow():
    """测试多智能体工作流"""
    # 设置模拟环境
    with patch('model.call', return_value=mock_response):
        # 执行工作流
        result = await travel_planning_workflow("杭州")

        # 验证结果
        assert "天气" in result
        assert "景点" in result
```

#### 7.3.2 工具集成测试

```python
@pytest.mark.asyncio
async def test_tool_integration_with_agent():
    """测试智能体与工具集成"""
    from agentscope.message import Msg
    # 创建带工具的智能体
    toolkit = Toolkit()
    toolkit.register_tool_function(weather_tool, group_name="weather")
    toolkit.register_tool_function(news_tool, group_name="news")
    agent = ReActAgent(
        toolkit=toolkit
    )

    # 测试工具调用
    result = await agent(Msg(name="user", content="杭州天气怎么样？", role="user"))

    assert "天气" in result.content
    assert weather_tool.called
```

### 7.4 端到端测试

#### 7.4.1 完整场景测试

```python
@pytest.mark.asyncio
async def test_end_to_end_customer_service():
    """端到端客服场景测试"""
    # 准备测试环境
    test_db = setup_test_database()
    test_client = TestClient(app)

    # 模拟用户请求
    response = test_client.post("/chat", json={
        "message": "我想查询订单 #12345 的状态",
        "user_id": "test_user"
    })

    # 验证响应
    assert response.status_code == 200
    data = response.json()
    assert "order_id" in data
    assert data["order_id"] == "12345"

    # 清理
    teardown_test_database(test_db)
```

#### 7.4.2 LLM-as-Judge 评估

```python
from agentscope.evaluate import GeneralEvaluator
from agentscope.message import Msg

evaluator = GeneralEvaluator(
    # 评估配置
)

async def test_response_quality():
    """测试响应质量"""
    response = await agent(Msg(name="user", content="解释量子计算", role="user"))

    scores = await evaluator.evaluate(
        response=response,
        criteria=["准确性", "完整性"]
    )

    assert scores["准确性"] > 0.8
    assert scores["完整性"] > 0.7
```

### 7.5 回归测试

#### 7.5.1 金丝雀数据集

```python
def test_no_regression():
    """确保新版本性能不下降"""
    current_scores = run_golden_dataset(current_agent)
    previous_scores = load_previous_scores("v1.2_scores.json")

    avg_current = sum(current_scores) / len(current_scores)
    avg_previous = sum(previous_scores) / len(previous_scores)

    # 允许 5% 性能下降容忍
    assert avg_current >= avg_previous * 0.95, "性能回归检测到"
```

### 7.6 测试最佳实践

| 实践 | 说明 |
|------|------|
| 测试金字塔 | 70% 单元、25% 集成、5% 端到端 |
| 默认使用 Mock | 单元/集成测试中 Mock LLM 调用 |
| 测试失败模式 | 验证错误处理和边界情况 |
| 自动化 CI/CD | 每次提交运行单元测试，PR 运行集成测试 |
| 黄金数据集 | 维护黄金输入/输出对用于回归测试 |

---

## 八、来源链接

本文档所有内容均来自以下实际访问的网页资源：

1. ReAct vs Plan-and-Execute 对比 - https://dev.to/jamesli/react-vs-plan-and-execute-a-practical-comparison-of-llm-agent-patterns-4gh9
2. IBM ReAct Agent 详解 - https://www.ibm.com/think/topics/react-agent
3. Prompt Engineering Guide LLM Agents - https://www.promptingguide.ai/research/llm-agents
4. AgentScope 核心概念 - https://doc.agentscope.io/zh_CN/tutorial/quickstart_key_concept.html
5. AgentScope 智能体教程 - https://doc.agentscope.io/zh_CN/tutorial/task_agent.html
6. AgentScope 模型教程 - https://doc.agentscope.io/zh_CN/tutorial/task_model.html
7. Medium RAG 最佳实践 - https://medium.com/@marcharaoui/chapter-5-best-practices-for-rag-7770fce8ac81
8. Stack Overflow RAG 实用技巧 - https://stackoverflow.blog/2024/08/15/practical-tips-for-retrieval-augmented-generation-rag/
9. Gradient Flow RAG 最佳实践 - https://gradientflow.substack.com/p/best-practices-in-retrieval-augmented
10. 企业 AI 安全最佳实践 - https://blog.premai.io/enterprise-ai-security-12-best-practices-for-deploying-llms-in-production/
11. DEV Community AI Agent 安全指南 - https://dev.to/theaniketgiri/building-production-ready-ai-agents-a-complete-security-guide-2026-4d01
12. Athenic Agent 测试策略 - https://getathenic.com/blog/agent-testing-strategies-unit-integration-e2e
13. MyEngineeringPath AI Agent 测试 - https://myengineeringpath.dev/genai-engineer/agent-testing/
14. Arsum Agentic AI 框架对比 - https://arsum.com/blog/posts/agentic-ai-frameworks-comparison/
15. instinctools 框架对比 - https://www.instinctools.com/blog/autogen-vs-langchain-vs-crewai/
16. Medium AI Agent 架构指南 - https://andriifurmanets.com/blogs/ai-agents-2026-practical-architecture-tools-memory-evals-guardrails

---

## 九、总结

本文档涵盖了 AgentScope 智能体开发的最佳实践，包括：

1. **设计模式**：ReAct 模式适合简单直接任务，Plan-and-Execute 适合复杂多步骤任务，多智能体协作适合分工场景
2. **Prompt Engineering**：清晰明确、结构化输出、约束条件、Few-Shot、思维链、角色扮演
3. **工具优化**：单一职责、清晰参数描述、并行调用、错误处理
4. **RAG 优化**：分块策略选择、嵌入模型选型、混合检索、上下文压缩、引用标注
5. **生产部署**：本地/Docker/K8s 部署、缓存策略、异步优化、OTel 监控
6. **安全性**：OWASP LLM Top 10 防护、输入验证、工具权限控制、输出过滤
7. **测试策略**：70% 单元 / 25% 集成 / 5% E2E 的测试金字塔、Mock LLM 调用、黄金数据集回归测试

建议结合 [reference_official_docs.md](reference_official_docs.md) 了解框架核心概念和 API 参考。

---

*文档版本：2026年4月*
*最后更新：2026年4月30日*
