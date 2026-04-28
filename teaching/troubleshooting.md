# AgentScope 故障排除指南

本文档汇总了 AgentScope 官方文档和社区讨论中的常见问题与解决方案。

---

## 1. 安装与配置问题

### 1.1 安装失败

**问题**: `pip install agentscope` 安装失败

**解决方案**:
```bash
# 确保使用 Python 3.10+
python --version

# 使用虚拟环境
python -m venv venv
source venv/bin/activate

# 安装最新版本
pip install agentscope --upgrade
```

### 1.2 Runtime 安装问题

**问题**: `agentscope-runtime` 安装不完整

**解决方案**:
```bash
# 基础安装
pip install agentscope-runtime

# Kubernetes 部署需要扩展依赖
pip install 'agentscope-runtime[ext]>=1.0.0'
```

### 1.3 API Key 配置问题

**问题**: 模型调用报错 `API key not found`

**解决方案**:
```bash
# 设置环境变量
export DASHSCOPE_API_KEY="your_api_key_here"

# 或在代码中指定
model = DashScopeChatModel(
    model_name="qwen-turbo",
    api_key="your_api_key_here",  # 不推荐硬编码
)
```

---

## 2. Agent 开发问题

### 2.1 AgentApp 未找到

**错误**: `No AgentApp found in agent.py`

**解决方案**:
- 确保文件导出 `agent_app` 或 `app` 变量
- 或创建 `create_app()` 函数

```python
# 方式1: 直接导出
agent_app = AgentApp(...)

# 方式2: 使用工厂函数
def create_app():
    return AgentApp(...)

# 方式3: 创建 app.py
app = create_app()
```

### 2.2 多个 AgentApp 实例

**错误**: `Multiple AgentApp instances found`

**解决方案**: 确保只导出一个 AgentApp 实例

### 2.3 Agent 无响应

**可能原因**:
1. 模型 API 调用超时
2. 工具执行卡住
3. 内存耗尽

**解决方案**:
```python
# 设置超时
agent = ReActAgent(
    name="Friday",
    model=DashScopeChatModel(
        model_name="qwen-turbo",
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        generate_kwargs={"timeout": 30}
    ),
    ...
)
```

### 2.4 对话历史不保留

**问题**: 每次请求都是新的对话

**解决方案**:
```python
# 使用 InMemoryMemory 保存对话历史
agent = ReActAgent(
    ...
    memory=InMemoryMemory(),
)

# 生产环境使用 RedisSession
from agentscope.session import RedisSession
session = RedisSession(connection_pool=redis_pool)
```

---

## 3. 模型相关问题

### 3.1 模型不支持工具调用

**问题**: 模型返回的不是预期的工具调用格式

**解决方案**:
- 检查模型是否支持工具调用
- 使用支持工具调用的模型（如 qwen-max）
- 配置正确的工具 JSON Schema

### 3.2 vLLM 工具调用问题

**问题**: vLLM 模型工具调用失败

**解决方案**:
```bash
# 启动 vLLM 时配置工具调用参数
vllm serve your-model \
    --enable-auto-tool-choice \
    --tool-call-parser your-parser
```

### 3.3 流式输出不工作

**问题**: 设置 `stream=True` 但没有流式输出

**解决方案**:
```python
# 确保正确处理异步生成器
model = DashScopeChatModel(
    model_name="qwen-turbo",
    stream=True,
)

# 流式输出直接返回 AsyncGenerator，不需要 await
async for chunk in model(messages):
    print(chunk)
```

### 3.4 推理模式（Thinking）问题

**问题**: 启用思考模式后响应慢

**解决方案**:
- 思考模式会增加延迟，这是正常现象
- 仅在需要复杂推理的任务时启用
- 可以设置 `max_tokens` 限制思考长度

### 3.5 vLLM 重复调用内存错误

**问题**: [GitHub Issue #1022](https://github.com/agentscope-ai/agentscope/issues/1022) - 重复调用同一 Agent 实例导致内存处理错误

**错误信息**:
```
openai.BadRequestError: 4 validation errors for ValidatorIterator
0.ChatCompletionContentPartTextParam.text
  Input should be a valid string [type=string_type, input_value=None]
```

**根本原因**: 
当使用 `InMemoryMemory` 时，内存中保存的消息包含 `{"text": None}` 格式的空内容，发送给 vLLM 时序列化为 `"content": [{"text": null}]` 导致验证失败。

**解决方案**:
1. 使用 `DashScopeChatFormatter` 而非 `OpenAIChatFormatter`（当模型是 DashScope 系列时）
2. 或在每次调用前清理 Agent 内存
3. 或升级到最新版本的 AgentScope

```python
# 方案：每次调用前清理内存
agent.memory.clear()
response = await agent(Msg("user", query, "user"))
```

### 3.6 vLLM + Qwen3.5 模型 Chat Template 错误

**问题**: [GitHub Issue #1352](https://github.com/agentscope-ai/agentscope/issues/1352) - vLLM 使用 qwen3.5 模型时 chat template 错误

**错误信息**:
```
jinja2.exceptions.TemplateError: No user query found in messages.
```

**解决方案**:
1. 确保 vLLM 版本兼容
2. 检查 `chat_template_kwargs` 参数配置
3. 使用 DashScope 官方模型或更新 vLLM 到最新版本

```python
# 正确配置示例
model = OpenAIChatModel(
    model_name="qwen",
    api_key="empty",
    client_kwargs={"base_url": "http://your-vllm:9999/v1/"},
    generate_kwargs={
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": False}
        }
    },
)
```

### 3.7 多Agent通信消息格式问题

**问题**: 使用 `OpenAIMultiAgentFormatter` 时消息格式不匹配

**解决方案**:
```python
# 正确导入
from agentscope.formatter import OpenAIMultiAgentFormatter

# 使用正确的 Formatter
agent = ReActAgent(
    ...
    formatter=OpenAIMultiAgentFormatter(),  # 用于多Agent通信
)
```

---

## 4. 部署问题

### 4.1 端口已被占用

**错误**: `Address already in use`

**解决方案**:
```bash
# 使用不同端口
agentscope run app_agent.py --port 8090

# 或停止占用端口的进程
lsof -i :8080
kill <PID>
```

### 4.2 部署超时

**问题**: Kubernetes/云端部署超时

**解决方案**:
1. 检查网络连接
2. 验证凭证正确性
3. 增加超时时间：

```bash
agentscope deploy k8s app_agent.py \
    --deploy-timeout 600 \
    --env DASHSCOPE_API_KEY=sk-xxx
```

### 4.3 镜像推送失败

**问题**: Docker 镜像无法推送到仓库

**解决方案**:
```bash
# 登录镜像仓库
docker login your-registry-url

# 检查 registry 配置
agentscope deploy k8s app_agent.py \
    --registry-url your-registry-url \
    --push
```

### 4.4 Session 不持久化

**问题**: 对话历史在重启后丢失

**解决方案**:
- 开发环境使用 `InMemoryMemory`
- 生产环境使用 `RedisSession`

```python
# 生产配置
from agentscope.session import RedisSession
import fakeredis

fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
session = RedisSession(connection_pool=fake_redis.connection_pool)
```

### 4.5 速率限制 (429 Too Many Requests)

**问题**: API 调用触发速率限制

**解决方案**:
1. 实施指数退避重试
2. 使用 Token 级限流而非请求级限流
3. 配置 AI Gateway 统一管理限流

```python
import asyncio
import time

async def retry_with_backoff(func, max_retries=3, base_delay=1):
    for attempt in range(max_retries):
        try:
            return await func()
        except HTTPError as e:
            if e.status_code == 429:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                raise
    raise Exception("Max retries exceeded")
```

### 4.6 Kubernetes 部署内存溢出 (OOMKilled)

**问题**: Pod 被 Kubernetes 杀死，状态为 OOMKilled

**解决方案**:
1. 增加内存限制
2. 优化 Agent 内存使用（减少 context window）
3. 使用流式响应减少峰值内存

```bash
# 部署时配置资源
agentscope deploy k8s app_agent.py \
  --memory-limit 4Gi \
  --memory-request 2Gi
```

---

## 5. Runtime CLI 问题

### 5.1 命令未找到

**问题**: `agentscope: command not found`

**解决方案**:
```bash
# 重新安装
pip install agentscope-runtime --force-reinstall

# 验证安装
agentscope --version
```

### 5.2 版本不匹配

**问题**: Runtime 版本与 AgentScope 版本不兼容

**解决方案**:
```bash
# 更新到兼容版本
pip install agentscope --upgrade
pip install agentscope-runtime --upgrade

# 检查版本
pip show agentscope
pip show agentscope-runtime
```

### 5.3 状态文件损坏

**问题**: `deployments.json` 损坏

**解决方案**:
```bash
# 查看备份
ls ~/.agentscope-runtime/deployments.backup*.json

# 从备份恢复
cp ~/.agentscope-runtime/deployments.backup.20240101.json \
   ~/.agentscope-runtime/deployments.json
```

---

## 6. 沙箱相关问题

### 6.1 沙箱工具执行失败

**问题**: `execute_python_code` 等沙箱工具无法执行

**解决方案**:
- 检查沙箱依赖是否安装
- 确认工作目录权限

### 6.2 沙箱与宿主机隔离

**重要安全准则**:
> 永远不要在宿主机上重新运行沙箱验证的操作。

正确的做法：
- 让代理写入沙箱工作目录
- 直接使用沙箱输出（下载的文件等）
- 不要尝试在宿主机复现沙箱内的操作

---

## 7. 追踪与调试问题

### 7.1 追踪数据未显示

**问题**: AgentScope Studio 不显示追踪数据

**解决方案**:
```python
import agentscope

# 初始化时连接 Studio
agentscope.init(studio_url="http://localhost:port")
```

### 7.2 Token 使用数据缺失

**问题**: 无法获取 Token 使用统计

**解决方案**:
```python
res = await model(messages)
print(f"Usage: {res.usage}")
```

### 7.3 追踪后端连接失败

**问题**: 无法发送到 OTLP 后端

**解决方案**:
```python
import agentscope

# 检查 tracing_url 配置
agentscope.init(tracing_url="https://your-backend:4318/v1/traces")

# 阿里云 CloudMonitor
agentscope.init(tracing_url="https://tracing-cn-hangzhou.arms.aliyuncs.com/adapt_xxx/api/otlp/traces")
```

---

## 8. 评估问题

### 8.1 评估任务失败

**问题**: ACEBench 评估报错

**解决方案**:
```python
# 使用 GeneralEvaluator 进行调试
evaluator = GeneralEvaluator(
    name="evaluation",
    benchmark=ACEBenchmark(),
    n_workers=1,  # 单线程便于调试
    storage=FileEvaluatorStorage(save_dir="./results"),
)
```

### 8.2 评估结果不一致

**问题**: 多次运行同一任务结果不同

**解决方案**:
- 设置随机种子: `generate_kwargs={"seed": 42}`
- 增加评估次数: `n_repeat=3`
- 检查模型温度设置

---

## 9. 云服务特定问题

### 9.1 ModelStudio 部署问题

**问题**: `PAI deployer is not available`

**解决方案**:
```bash
pip install 'agentscope-runtime[ext]>=1.0.0'
```

### 9.2 STS 凭证过期

**问题**: 使用 STS 临时凭证部署失败

**解决方案**:
- STS 令牌有时效性，重新获取
- 检查 `ALIBABA_CLOUD_SECURITY_TOKEN` 环境变量

### 9.3 OSS 上传失败

**问题**: 构建产物上传到 OSS 失败

**解决方案**:
1. 确认 OSS bucket 存在
2. 检查 region 配置
3. 验证 AK/SK 权限

---

## 10. 常见错误代码

| 错误代码 | 含义 | 解决方案 |
|---------|-----|---------|
| `API_KEY_INVALID` | API Key 无效 | 检查环境变量配置 |
| `MODEL_NOT_FOUND` | 模型不存在 | 检查模型名称 |
| `TIMEOUT` | 请求超时 | 增加超时或检查网络 |
| `RATE_LIMIT` | 速率限制 | 实施请求限流 |
| `SESSION_NOT_FOUND` | Session 不存在 | 检查 session_id |
| `DEPLOYMENT_FAILED` | 部署失败 | 查看详细日志 |

---

## 11. 调试技巧

### 11.1 启用详细输出

```bash
agentscope chat app_agent.py --verbose
```

### 11.2 查看构建缓存

```bash
ls -la .agentscope_runtime/builds/
```

### 11.3 清理旧构建

```bash
rm -rf .agentscope_runtime/builds/*
```

### 11.4 检查部署状态

```bash
agentscope list
agentscope status <deployment-id>
```

---

## 12. 获取帮助

### 12.1 官方资源

- 官方文档: https://doc.agentscope.io/
- Runtime 文档: https://runtime.agentscope.io/
- GitHub Issues: https://github.com/agentscope-ai/agentscope/issues
- 示例代码: https://agentscope.io/samples/

### 12.2 社区支持

- Discord 社区
- DingTalk 群组

### 12.3 报告问题

报告 Bug 或安全漏洞：
- GitHub Issues: https://github.com/agentscope-ai/agentscope/issues
- 安全问题: 通过 private disclosure 报告

---

## 13. FAQ 速查

| 问题 | 答案 |
|-----|-----|
| v1.0 与 v0.x 的主要区别？ | v1.0 完全重构，废弃拖拽式工作站，推荐代码优先开发模式 |
| 支持哪些模型？ | DashScope, Gemini, OpenAI, Anthropic, Ollama, vLLM, DeepSeek |
| 如何监控 Token 使用？ | 使用 AgentScope Studio 或连接 OTLP 后端 |
| 生产环境用哪个 Session？ | 必须使用 RedisSession，InMemoryMemory 仅用于开发 |
| 如何实现结构化输出？ | 使用 Pydantic 模型配合 `structured_model` 参数 |
