# AgentScope 最佳实践指南

本文档汇总了 AgentScope 官方文档中的最佳实践，涵盖开发、部署、性能优化和安全等关键领域。

---

## 1. 开发最佳实践

### 1.1 Agent 开发模式

#### 使用 ReActAgent 进行开发
ReActAgent 是官方推荐的构建代理应用的主要类，它实现了 Reason + Act 范式：

```python
import os
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit

agent = ReActAgent(
    name="Friday",
    model=DashScopeChatModel("qwen-turbo", api_key=os.getenv("DASHSCOPE_API_KEY")),
    sys_prompt="You're a helpful assistant named Friday.",
    toolkit=Toolkit(),
    memory=InMemoryMemory(),
    formatter=DashScopeChatFormatter(),
)
```

#### Agent 类层次结构
- `agent_base`: 抽象基类，定义核心代理接口
- `react_agent_base`: 实现 ReAct 范式，处理思考-行动循环
- `react_agent`: 具体可用代理，扩展 react_agent_base 并集成工具

### 1.2 模型配置最佳实践

#### 模型参数预设
通过 `generate_kwargs` 预设模型行为参数：

```python
import os
from agentscope.model import DashScopeChatModel

model = DashScopeChatModel(
    model_name="qwen-max",
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    generate_kwargs={
        "temperature": 0.3,
        "max_tokens": 1000
    }
)
```

#### OpenAI 兼容模型配置
对于 vLLM、DeepSeek 等 OpenAI 兼容模型：

```python
from agentscope.model import OpenAIChatModel

OpenAIChatModel(client_kwargs={"base_url": "http://localhost:8000/v1"})
```

### 1.3 消息处理

#### 使用统一的 ToolUseBlock 和 ToolResultBlock
不同模型提供商的工具 API 格式不同，AgentScope 通过统一接口解决：

```python
json_schemas = [
    {
        "type": "function",
        "function": {
            "name": "google_search",
            "description": "Search for a query on Google.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                },
                "required": ["query"],
            },
        },
    },
]
```

#### 消息流式处理
使用 `stream_printing_messages` 实现流式响应：

```python
from agentscope.pipeline import stream_printing_messages

async for msg, last in stream_printing_messages(
    agents=[agent],
    coroutine_task=agent(msgs),
):
    yield msg, last
```

### 1.4 推理模型支持

启用思考模式处理复杂推理任务：

```python
import os
from agentscope.model import DashScopeChatModel

model = DashScopeChatModel(
    model_name="qwen-turbo",
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    enable_thinking=True,
)
```

### 1.5 结构化输出

AgentScope 支持动态 Pydantic 模型进行结构化输出：

```python
from pydantic import BaseModel

class ToyBenchAnswerFormat(BaseModel):
    answer_as_number: float

res = await agent(msg_input, structured_model=ToyBenchAnswerFormat)
```

---

## 2. 部署最佳实践

### 2.1 部署模式选择

| 部署类型 | 适用场景 | 可扩展性 |
|---------|---------|---------|
| Local Daemon | 开发测试 | 单进程 |
| Detached Process | 生产服务 | 单节点 |
| Kubernetes | 企业级云部署 | 多节点 |
| ModelStudio | 阿里云托管部署 | 云管理 |
| Knative | Serverless | 自动扩缩 |
| Kruise | 隔离环境 | 实例级隔离 |

### 2.2 本地开发部署

```bash
# 开发测试
agentscope chat app_agent.py

# 部署为本地服务
agentscope deploy local app_agent.py --env DASHSCOPE_API_KEY=sk-xxx
```

### 2.3 Kubernetes 部署

```python
from agentscope_runtime.engine.deployers.kubernetes_deployer import (
    KubernetesDeployManager, RegistryConfig, K8sConfig
)

deployer = KubernetesDeployManager(
    kube_config=K8sConfig(k8s_namespace="agentscope-runtime"),
    registry_config=RegistryConfig(registry_url="your-registry-url"),
)

result = await app.deploy(
    deployer,
    port="8080",
    replicas=3,
    image_name="agent_app",
    image_tag="v1.0",
    runtime_config={
        "resources": {
            "requests": {"cpu": "200m", "memory": "512Mi"},
            "limits": {"cpu": "1000m", "memory": "2Gi"},
        },
    },
)
```

### 2.4 阿里云 ModelStudio 部署

支持 STS 临时凭证认证，增强安全性：

```python
import os
from agentscope_runtime.engine.deployers.modelstudio_deployer import (
    ModelstudioDeployManager, OSSConfig, ModelstudioConfig
)

deployer = ModelstudioDeployManager(
    oss_config=OSSConfig(...),
    modelstudio_config=ModelstudioConfig(
        workspace_id=os.environ.get("MODELSTUDIO_WORKSPACE_ID"),
        access_key_id=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"),
        security_token=os.environ.get("ALIBABA_CLOUD_SECURITY_TOKEN"),
        dashscope_api_key=os.environ.get("DASHSCOPE_API_KEY"),
    ),
)
```

### 2.5 服务配置最佳实践

#### 生产环境使用 Redis Session

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from agentscope.session import RedisSession

@asynccontextmanager
async def lifespan(app: FastAPI):
    import fakeredis
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.state.session = RedisSession(connection_pool=fake_redis.connection_pool)
    yield
```

**重要**: 开发测试使用 `InMemoryMemory`，生产环境必须使用 Redis 或持久化存储。

---

## 3. 性能优化最佳实践

### 3.1 异步执行

使用异步工具执行避免阻塞：

```python
from agentscope.pipeline import stream_printing_messages

async for msg, last in stream_printing_messages(
    agents=[agent],
    coroutine_task=agent(msgs),
):
    yield msg, last
```

### 3.2 流式响应

启用流式模式减少感知延迟：

```python
import os
from agentscope.model import DashScopeChatModel

model = DashScopeChatModel(
    model_name="qwen-turbo",
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    stream=True,
)
```

### 3.3 资源管理

#### 合理配置资源限制
```python
runtime_config={
    "resources": {
        "requests": {"cpu": "200m", "memory": "512Mi"},
        "limits": {"cpu": "1000m", "memory": "2Gi"},
    },
}
```

#### 模型分级
- 高优先级流量使用大容量模型
- 低优先级或批处理任务使用小型廉价模型

### 3.4 自动扩缩容

在 Kubernetes/Knative 环境中配置 HPA（水平Pod自动扩缩容）。

---

## 4. 安全最佳实践

### 4.1 密钥管理

#### 使用环境变量
```bash
export DASHSCOPE_API_KEY="your_api_key_here"
```

#### 阿里云 STS 临时凭证
```python
import os

os.environ["ALIBABA_CLOUD_SECURITY_TOKEN"] = "your-sts-token"
```

### 4.2 沙箱执行

AgentScope Runtime 提供沙箱隔离执行工具：
- execute_python_code
- execute_shell_command
- write_text_file

**安全准则**: 永远不要在宿主机上重新运行沙箱验证的操作。

### 4.3 网络隔离

- 生产环境使用 VPC 隔离
- 配置安全组规则
- 使用私有网络访问云服务

---

## 5. 可观测性最佳实践

### 5.1 OpenTelemetry 追踪

AgentScope 内置 OpenTelemetry 支持，追踪 LLM 调用、工具执行等：

```python
import agentscope

# AgentScope Studio 可视化
agentscope.init(studio_url="http://localhost:port")

# 或发送到第三方 OTLP 后端
agentscope.init(tracing_url="https://your-backend:4318/v1/traces")
```

### 5.2 支持的追踪后端

| 后端 | 配置方式 |
|-----|---------|
| AgentScope Studio | `studio_url` |
| 阿里云 CloudMonitor | OTLP 端点 |
| Arize Phoenix | PHOENIX_API_KEY |
| Langfuse | Authorization Basic |

### 5.3 自定义追踪装饰器

```python
from agentscope.tracing import trace_llm, trace_reply, trace_format
from agentscope.model import ChatModelBase
from agentscope.agent import AgentBase

class MyChatModel(ChatModelBase):
    @trace_llm
    async def __call__(self, messages, **kwargs):
        ...

class MyAgent(AgentBase):
    @trace_reply
    async def reply(self, *args, **kwargs):
        ...
```

### 5.4 Token 使用监控

Token 使用信息包含在 ChatResponse 中：

```python
# 假设 model 是已配置的模型实例
res = await model(messages)
print(f"Input tokens: {res.usage.input_tokens}")
print(f"Output tokens: {res.usage.output_tokens}")
```

---

## 6. 评估最佳实践

### 6.1 使用 ACEBench 进行基准测试

```python
from agentscope.evaluate import GeneralEvaluator, FileEvaluatorStorage, ACEBenchmark

evaluator = GeneralEvaluator(
    name="benchmark evaluation",
    benchmark=ACEBenchmark(),
    n_repeat=1,
    storage=FileEvaluatorStorage(save_dir="./results"),
    n_workers=1,
)
await evaluator.run(solution_generation_fn)
```

### 6.2 分布式评估

使用 RayEvaluator 进行并行分布式评估：

```python
from agentscope.evaluate import RayEvaluator, ACEBenchmark, FileEvaluatorStorage

evaluator = RayEvaluator(
    name="distributed evaluation",
    benchmark=ACEBenchmark(),
    n_repeat=1,
    storage=FileEvaluatorStorage(save_dir="./results"),
)
```

---

## 7. 各模型服务商 API 最佳实践

### 7.1 OpenAI

```python
import os
from agentscope.model import OpenAIChatModel

OpenAIChatModel(
    model_name="gpt-4",
    api_key=os.getenv("OPENAI_API_KEY"),
    generate_kwargs={"temperature": 0.7, "max_tokens": 1000}
)
```

### 7.2 Anthropic

```python
import os
from agentscope.model import AnthropicChatModel

AnthropicChatModel(
    model_name="claude-3-sonnet",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)
```

### 7.3 阿里云 DashScope

```python
import os
from agentscope.model import DashScopeChatModel

DashScopeChatModel(
    model_name="qwen-max",
    api_key=os.getenv("DASHSCOPE_API_KEY"),
)
```

### 7.4 Google Gemini

```python
import os
from agentscope.model import GeminiChatModel

GeminiChatModel(
    model_name="gemini-pro",
    api_key=os.getenv("GEMINI_API_KEY"),
)
```

### 7.5 Ollama (本地模型)

```python
from agentscope.model import OllamaChatModel

OllamaChatModel(
    model_name="llama2",
    client_kwargs={"base_url": "http://localhost:11434/v1"}
)
```

### 7.6 vLLM/DeepSeek

```python
from agentscope.model import OpenAIChatModel

OpenAIChatModel(
    client_kwargs={
        "base_url": "http://your-vllm-endpoint/v1",
        "api_key": "your-api-key"
    }
)
```

**注意**: 使用 vLLM 时需配置工具调用参数，如 `--enable-auto-tool-choice`, `--tool-call-parser` 等。

---

## 8. CI/CD 最佳实践

### 8.1 版本锁定

在 CI 工件中固定 AgentScope runtime 版本：

```yaml
dependencies:
  - agentscope-runtime>=1.0.0
```

### 8.2 评估门禁

将 ACEBench/RayEvaluator 集成到 CI：

```bash
# 在合并前运行评估
agentscope evaluate --benchmark ACEBench --threshold 0.8
```

### 8.3 容器镜像构建

```bash
agentscope deploy k8s app_agent.py \
  --image-name agent_app \
  --image-tag linux-amd64-$(git rev-parse --short HEAD) \
  --registry-url your-registry.com \
  --push \
  --requirements requirements.txt
```

---

## 9. 生产就绪检查清单

- [ ] AgentScope runtime 版本已固定
- [ ] 使用 Redis 替代 InMemoryMemory
- [ ] OpenTelemetry 追踪已启用
- [ ] 评估测试已集成到 CI
- [ ] 密钥使用环境变量或 STS
- [ ] 工具执行使用沙箱隔离
- [ ] 配置了资源限制
- [ ] 部署了健康检查
- [ ] 实现了回滚策略

---

## 10. 多Agent系统设计模式

### 10.1 架构模式选择

| 模式 | 适用场景 | 优点 | 缺点 |
|-----|---------|-----|-----|
| Hub-and-Spoke (协调者-工作者) | 任务分解、中心调度 | 简单、可控 | 协调者可能成为瓶颈 |
| Flat Mesh (点对点) | 对等协作、辩论 | 平等交互、去中心化 | 难以协调、调试复杂 |
| Hierarchical (层级) | 复杂任务分解 | 可扩展、分层治理 | 实现复杂 |

### 10.2 AgentScope 实现多Agent协作

#### MsgHub 协调模式
```python
import asyncio
from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.pipeline import MsgHub, sequential_pipeline

async with MsgHub(
    participants=[agent1, agent2, agent3],
    announcement=Msg("Host", "Introduce yourselves.", "assistant"),
) as hub:
    await sequential_pipeline([agent1, agent2, agent3])

    # 动态添加/删除Agent
    hub.add(agent4)
    hub.delete(agent3)

    await hub.broadcast(Msg("Host", "Wrap up."), to=[])
```

#### 辩论系统实现
```python
import asyncio
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIMultiAgentFormatter
from agentscope.message import Msg
from agentscope.pipeline import MsgHub

# 定义辩论主题
DEBATE_TOPIC = "Should AGI research be open-sourced?"

def make_model():
    # 请替换为实际的模型配置
    from agentscope.model import OpenAIChatModel
    return OpenAIChatModel(model_name="gpt-4", api_key="your-api-key")

proponent = ReActAgent(
    name="Proponent",
    sys_prompt=f"You argue IN FAVOR of {DEBATE_TOPIC}",
    model=make_model(),
    formatter=OpenAIMultiAgentFormatter(),
)

opponent = ReActAgent(
    name="Opponent",
    sys_prompt=f"You argue AGAINST {DEBATE_TOPIC}",
    model=make_model(),
    formatter=OpenAIMultiAgentFormatter(),
)

# 多轮辩论
for round_num in range(num_rounds):
    async with MsgHub(participants=[proponent, opponent]):
        pro_msg = await proponent(Msg("Moderator", "Present your argument.", "user"))
        opp_msg = await opponent(Msg("Moderator", "Respond.", "user"))
```

### 10.3 并发多Agent流水线

```python
import asyncio
from agentscope.agent import ReActAgent
from agentscope.message import Msg

async def concurrent_analysis(topic: str):
    specialists = {
        "Economist": "Analyze from an economic perspective in 2-3 sentences.",
        "Ethicist": "Analyze from an ethical perspective in 2-3 sentences.",
        "Technologist": "Analyze from a technology perspective in 2-3 sentences.",
    }

    def make_model():
        from agentscope.model import OpenAIChatModel
        return OpenAIChatModel(model_name="gpt-4", api_key="your-api-key")

    agents = [
        ReActAgent(name=name, sys_prompt=prompt, model=make_model())
        for name, prompt in specialists.items()
    ]

    topic_msg = Msg("user", topic, "user")

    # 并发执行
    results = await asyncio.gather(*(agent(topic_msg) for agent in agents))

    # 聚合结果
    synthesizer = ReActAgent(
        name="Synthesiser",
        sys_prompt="Combine perspectives into a coherent summary...",
        model=make_model()
    )
    combined_text = "\n\n".join(
        f"[{agent.name}]: {r.get_text_content()}" for agent, r in zip(agents, results)
    )
    synthesis = await synthesizer(Msg("user", combined_text, "user"))
```

---

## 11. RAG + Agent 结合模式

### 11.1 Agentic RAG 架构

| 组件 | 职责 |
|-----|-----|
| 查询代理 (Query Agent) | 理解用户意图，生成检索策略 |
| 检索代理 (Retrieval Agent) | 执行向量检索、过滤、重排序 |
| 生成代理 (Generative Agent) | 综合检索结果生成最终答案 |

### 11.2 RAG 查询生成模式

```python
from agentscope.agent import ReActAgent
from agentscope.message import Msg

# 多数据库查询生成
# Rquery = gdb(Qgenerated, Dconnection)

# 查询重写与扩展
def make_model():
    from agentscope.model import OpenAIChatModel
    return OpenAIChatModel(model_name="gpt-4", api_key="your-api-key")

async def query_augmentation(original_query: str) -> list[str]:
    agent = ReActAgent(
        name="QueryAugmenter",
        sys_prompt="Generate 3 alternative queries for the same intent.",
        model=make_model()
    )
    # 返回多个查询变体以提高召回率
```

### 11.3 AgentScope 中的 RAG 集成

```python
from agentscope.agent import ReActAgent
from agentscope.tool import Toolkit

# 注意: agentscope.rag 是可选模块，需要额外安装
try:
    from agentscope.rag import KnowledgeBase, Retriever
except ImportError:
    KnowledgeBase = None  # type: ignore
    Retriever = None  # type: ignore
    print("Warning: agentscope.rag not installed. RAG features unavailable.")

def make_model():
    from agentscope.model import OpenAIChatModel
    return OpenAIChatModel(model_name="gpt-4", api_key="your-api-key")

# 创建知识库 (假设使用向量数据库)
# kb = KnowledgeBase(
#     documents=document_list,
#     embed_model="text-embedding-3-small"
# )

# 在Agent中使用RAG
agent = ReActAgent(
    name="RAGAssistant",
    model=make_model(),
    toolkit=Toolkit([
        # RetrieverTool(kb),  # 检索工具
        # 其他业务工具...
    ]),
)
```

---

## 12. LLM API 成本优化策略

### 12.1 Token 优化核心原则

输出 Token 通常比输入 Token 贵 3-5 倍（部分模型达 8 倍），优化应优先减少输出。

### 12.2 立即见效的优化（10-30% 节省）

| 策略 | 实施方法 |
|-----|---------|
| Prompt 精简 | 移除不必要的 Token，控制在最小必要范围 |
| 输出长度控制 | 通过 `max_tokens` 限制最大输出 |
| 提示缓存 | 使用 Provider 原生缓存机制 |
| 模型降级 | 简单任务使用小型/廉价模型 |

### 12.3 RAG 优化

```python
# 避免传递完整文档
# 错误：传递 4-8 个完整文档/查询
# 正确：只传递相关片段
retrieved_chunks = await kb.similarity_search(
    query, 
    top_k=3,  # 只取最相关的3个片段
    max_length=500  # 限制每个片段长度
)
```

### 12.4 语义缓存

```python
from redis import Redis
from semantic_cache import SemanticCache

cache = SemanticCache(
    redis_client=Redis(host='localhost'),
    embed_model="text-embedding-3-small",
    similarity_threshold=0.95  # 相似度 > 95% 时命中缓存
)

# 检查缓存
cached_response = await cache.get(query)
if cached_response:
    return cached_response  # 避免 LLM 调用

# 缓存新响应
await cache.set(query, llm_response)
```

### 12.5 模型分级策略

| 任务类型 | 推荐模型 | 理由 |
|---------|---------|-----|
| 简单问答 | qwen-turbo / gpt-4o-mini | 成本低、延迟小 |
| 常规任务 | qwen-max / gpt-4 | 平衡性能与成本 |
| 复杂推理 | qwen-plus / claude-3-sonnet | 高质量输出 |

---

## 13. API 速率限制最佳实践

### 13.1 三层限制策略

| 层级 | 指标 | 用途 |
|-----|-----|-----|
| Token 级 | TPM (Tokens Per Minute) | 控制计算资源消耗 |
| 请求 级 | RPM (Requests Per Minute) | 控制 API 调用频率 |
| 成本 级 | $/小时或$/天 | 预算控制 |

### 13.2 AI Gateway 统一管理

```python
# 使用 Kong/Portkey 等 AI Gateway
# 在网关层统一配置限流策略

# 示例：Token 级限流配置
llm_config = {
    "rate_limit": {
        "tokens_per_minute": 100000,
        "requests_per_minute": 100,
    },
    "budget": {
        "daily_limit_usd": 50.0,
        "monthly_limit_usd": 500.0,
    }
}
```

### 13.3 应用级限流

```python
import time
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

class LLMRateLimiter:
    def __init__(self, tokens_per_minute: int, requests_per_minute: int):
        self.tpm = tokens_per_minute
        self.rpm = requests_per_minute
        self.tokens_used = []
        self.requests_made = []

    async def check_limit(self, tokens: int, user_id: str) -> bool:
        now = time.time()
        # 清理过期记录
        self.tokens_used = [t for t in self.tokens_used if now - t < 60]
        self.requests_made = [r for r in self.requests_made if now - r < 60]

        if len(self.requests_made) >= self.rpm:
            return False
        if sum(self.tokens_used) + tokens > self.tpm:
            return False

        self.tokens_used.append(tokens)
        self.requests_made.append(now)
        return True
```

---

## 14. 生产级 Agent 部署架构

### 14.1 Knative 自动扩缩容

```bash
agentscope deploy knative app_agent.py \
  --image-name agent_app \
  --image-tag v1.0 \
  --registry-url your-registry.com \
  --push \
  --env DASHSCOPE_API_KEY=sk-xxx
```

**Knative 优势**:
- 零到数千实例的自动扩缩
- 基于请求量的自动扩缩
- 版本管理和流量分配

### 14.2 Kruise 沙箱隔离部署

```python
import os
from agentscope_runtime.engine.deployers.kruise_deployer import KruiseDeployManager
from agentscope_runtime.engine.deployers.utils.docker_image_utils import RegistryConfig
from agentscope_runtime.engine.deployers.kubernetes_deployer import K8sConfig

# 每个Agent运行在独立沙箱环境中
deployer = KruiseDeployManager(
    kube_config=K8sConfig(k8s_namespace="agentscope-runtime"),
    registry_config=RegistryConfig(registry_url="your-registry"),
)

result = await app.deploy(deployer, port="8090")
```

### 14.3 阿里云函数计算 (FC) 无服务器部署

```python
import os
from agentscope_runtime.engine.deployers.fc_deployer import FCDeployManager
from agentscope_runtime.engine.deployers.agentrun_deployer import OSSConfig, FCConfig

deployer = FCDeployManager(
    oss_config=OSSConfig(...),
    fc_config=FCConfig(
        access_key_id=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"),
        account_id=os.environ.get("FC_ACCOUNT_ID"),
        cpu=2.0,
        memory=2048,
    ),
)

result = await app.deploy(
    deployer,
    deploy_name="agent-app",
    requirements=["agentscope", "fastapi"],
)
```

---

## 15. 参考资源

### 官方资源
- 官方文档: https://doc.agentscope.io/
- Runtime 文档: https://runtime.agentscope.io/
- GitHub: https://github.com/agentscope-ai/agentscope
- 示例代码: https://agentscope.io/samples/

### 深度文章
- [Analytics Vidhya: AgentScope AI Complete Guide](https://www.analyticsvidhya.com/blog/2026/01/agentscope-ai/)
- [MarkTechPost: Production Ready AgentScope Workflows](https://marktechpost.com/2026/04/01/how-to-build-production-ready-agentscope-workflows/)
- [arXiv: AgentScope 1.0 Paper](https://arxiv.org/pdf/2508.16279)

### 多Agent系统设计
- [Databricks: Agent System Design Patterns](https://docs.databricks.com/gcp/en/generative-ai/guide/agent-system-design-patterns)
- [Confluent: Event-Driven Multi-Agent Systems](https://www.confluent.io/blog/event-driven-multi-agent-systems/)
- [arXiv: Multi-Agent RAG System](https://arxiv.org/pdf/2412.05838)

### LLM 成本优化
- [Kellton: LLM Cost Optimization](https://www.kellton.com/kellton-tech-blog/llm-cost-optimization-api-burn-rate)
- [Redis: LLM Token Optimization](https://redis.io/blog/llm-token-optimization-speed-up-apps/)
- [Portkey: Rate Limiting for LLM](https://portkey.ai/blog/rate-limiting-for-llm-applications)
