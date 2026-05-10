# Embedding 与 Token 计数深度剖析

## 学习目标

> 学完本节，你将能够：
> - [L1 记忆] 列举 Embedding 和 Token 模块的核心类及其厂商实现
> - [L2 理解] 解释嵌入缓存的 Cache-Aside 工作原理和 FileEmbeddingCache 的 LRU 淘汰机制
> - [L3 应用] 使用 EmbeddingModelBase 和 TokenCounterBase 进行文本嵌入和 Token 估算
> - [L4 分析] 分析 OpenAI 图像 Token 计算的分块算法和不同计数策略的优劣

**预计时间**：30 分钟
**先修要求**：已完成 [Model 模型](module_model_deep.md)

## 目录

1. [模块概述](#1-模块概述)
2. [目录结构](#2-目录结构)
3. [Embedding 模块源码解读](#3-embedding-模块源码解读)
   - [EmbeddingModelBase 抽象基类](#31-embeddingmodelbase-抽象基类)
   - [EmbeddingResponse 响应模型](#32-embeddingresponse-响应模型)
   - [EmbeddingCacheBase 缓存抽象](#33-embeddingcachebase-缓存抽象)
   - [FileEmbeddingCache 文件缓存](#34-fileembeddingcache-文件缓存)
   - [OpenAI 文本嵌入实现](#35-openai-文本嵌入实现)
   - [其他厂商实现概览](#36-其他厂商实现概览)
4. [Token 计数模块源码解读](#4-token-计数模块源码解读)
   - [TokenCounterBase 抽象基类](#41-tokencounterbase-抽象基类)
   - [CharTokenCounter 字符计数器](#42-chartokencounter-字符计数器)
   - [OpenAITokenCounter tiktoken 实现](#43-openaitokencounter-tiktoken-实现)
   - [AnthropicTokenCounter API 计数](#44-anthropictokencounter-api-计数)
   - [其他计数器概览](#45-其他计数器概览)
5. [设计模式总结](#5-设计模式总结)
6. [代码示例](#6-代码示例)
7. [练习题](#7-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 Embedding 和 Token 模块的核心类和厂商实现 | 列举、识别 |
| 理解 | 解释嵌入缓存的工作原理和 Cache-Aside 模式 | 解释、描述 |
| 应用 | 使用 EmbeddingModelBase 和 TokenCounterBase 进行文本嵌入和 Token 估算 | 实现、配置 |
| 分析 | 分析 OpenAI 图像 Token 计算的分块算法 | 分析、追踪 |
| 评价 | 评价不同 Token 计数策略（精确 API vs 估算）的优劣 | 评价、推荐 |
| 创造 | 设计一个支持多模态嵌入的自定义缓存策略 | 设计、构建 |

## 先修检查

- [ ] 了解向量嵌入（Embedding）的基本概念
- [ ] 了解 Token 和 Tokenizer 的基本原理
- [ ] Python async/await 基础
- [ ] NumPy 基础（数组存储）

## Java 开发者对照

| AgentScope 概念 | Java 对应 | 说明 |
|----------------|-----------|------|
| `EmbeddingModelBase` | Spring AI `EmbeddingModel` | 向量嵌入抽象接口 |
| `EmbeddingCacheBase` | Spring `CacheManager` | 缓存抽象（Cache-Aside 模式） |
| `FileEmbeddingCache` | Caffeine/Ehcache 文件持久化 | 具体缓存实现 |
| `TokenCounterBase` | 无直接对应 | LLM 特有的 Token 计数需求 |
| `CharTokenCounter` | `String.length()` | 最简单的字符计数估算 |
| `OpenAITokenCounter` | tiktoken-java | 基于 BPE 的精确 Token 计数 |
| `EmbeddingResponse` | Spring AI `EmbeddingResponse` | 嵌入调用结果封装 |

---

## 1. 模块概述

> **交叉引用**: Embedding 模块是 RAG 系统的核心依赖，向量嵌入用于文档检索和相似度计算，详见 [Memory 与 RAG 模块](module_memory_rag_deep.md)。Token 计数模块被 TruncatedFormatterBase 用于消息截断，详见 [Formatter 模块](module_formatter_deep.md) 和 [Model 模块](module_model_deep.md)。Tracing 模块提供 `trace_embedding()` 装饰器追踪嵌入调用，详见 [Tracing 模块](module_tracing_deep.md)。

Embedding（向量嵌入）和 Token 计数是 LLM 应用的两大基础设施：

- **Embedding**: 将文本/图像转换为高维向量，用于语义搜索、相似度计算和 RAG 检索
- **Token 计数**: 估算消息消耗的 Token 数量，用于成本控制和消息截断

这两个模块虽然功能独立，但都是 Model 层的核心支撑，经常配合使用。

---

## 2. 目录结构

### Embedding 模块

```
embedding/
├── __init__.py                      # 导出接口
├── _embedding_base.py               # EmbeddingModelBase 抽象基类
├── _embedding_response.py           # EmbeddingResponse 响应模型
├── _embedding_usage.py              # EmbeddingUsage 用量统计
├── _cache_base.py                   # EmbeddingCacheBase 缓存抽象
├── _file_cache.py                   # FileEmbeddingCache 文件缓存
├── _openai_embedding.py             # OpenAI 文本嵌入
├── _dashscope_embedding.py          # DashScope 文本嵌入
├── _dashscope_multimodal_embedding.py  # DashScope 多模态嵌入
├── _gemini_embedding.py             # Gemini 文本嵌入
└── _ollama_embedding.py             # Ollama 文本嵌入
```

### Token 计数模块

```
token/
├── __init__.py                      # 导出接口
├── _token_base.py                   # TokenCounterBase 抽象基类
├── _char_token_counter.py           # 字符计数器（估算）
├── _openai_token_counter.py         # OpenAI tiktoken 计数器
├── _anthropic_token_counter.py      # Anthropic API 计数器
├── _gemini_token_counter.py         # Gemini 计数器
└── _huggingface_token_counter.py    # HuggingFace tokenizer 计数器
```

---

## 3. Embedding 模块源码解读

### 3.1 EmbeddingModelBase 抽象基类

```python showLineNumbers
class EmbeddingModelBase:
    model_name: str                    # 模型名称
    supported_modalities: list[str]    # 支持的模态（如 ["text"]）
    dimensions: int                    # 嵌入向量维度

    def __init__(self, model_name: str, dimensions: int) -> None:
        self.model_name = model_name
        self.dimensions = dimensions

    async def __call__(self, *args, **kwargs) -> EmbeddingResponse:
        raise NotImplementedError(...)
```

**关键设计**：
- 通过 `__call__` 使模型实例可直接调用：`response = await embedding_model(texts)`
- `dimensions` 允许在同一模型下选择不同的向量维度（如 OpenAI 的 256/512/1024/3072）
- `supported_modalities` 声明模型支持的输入类型

### 3.2 EmbeddingResponse 响应模型

```python showLineNumbers
@dataclass
class EmbeddingResponse(DictMixin):
    embeddings: List[Embedding]       # 嵌入向量列表（List[float]）
    id: str                           # 响应 ID
    created_at: str                   # 创建时间戳
    type: Literal["embedding"]        # 固定为 "embedding"
    usage: EmbeddingUsage | None      # API 用量统计
    source: Literal["cache", "api"]   # 数据来源：缓存 or API 调用
```

**亮点**：`source` 字段区分数据来源，便于监控缓存命中率和成本。

### 3.3 EmbeddingCacheBase 缓存抽象

```python showLineNumbers
class EmbeddingCacheBase:
    @abstractmethod
    async def store(self, embeddings, identifier, overwrite=False): ...
    @abstractmethod
    async def retrieve(self, identifier) -> List[Embedding] | None: ...
    @abstractmethod
    async def remove(self, identifier): ...
    @abstractmethod
    async def clear(self): ...
```

**Cache-Aside 模式**：嵌入模型在调用 API 前先检查缓存，命中则直接返回（`source="cache"`），未命中则调用 API 并存入缓存。

### 3.4 FileEmbeddingCache 文件缓存

基于 NumPy 二进制文件（`.npy`）的嵌入缓存实现：

| 特性 | 说明 |
|------|------|
| 存储格式 | NumPy `.npy` 二进制文件 |
| 文件名策略 | `sha256(json.dumps(identifier)) + ".npy"` |
| 缓存淘汰 | 支持按文件数量 (`max_file_number`) 和总大小 (`max_cache_size`) 淘汰 |
| 淘汰策略 | LRU（按文件修改时间排序，删除最旧的） |

> **Java 对照**: 类似 Caffeine Cache 的 `maximumSize()` 和 `maximumWeight()` 配置。

### 3.5 OpenAI 文本嵌入实现

```python showLineNumbers
class OpenAITextEmbedding(EmbeddingModelBase):
    supported_modalities = ["text"]

    def __init__(self, api_key, model_name, dimensions=1024,
                 embedding_cache=None, **kwargs):
        self.client = openai.AsyncClient(api_key=api_key, **kwargs)
        self.embedding_cache = embedding_cache

    async def __call__(self, text: List[str | TextBlock]) -> EmbeddingResponse:
        # 1. 提取文本内容
        gather_text = [t["text"] if isinstance(t, dict) else t for t in text]

        # 2. 检查缓存
        if self.embedding_cache:
            cached = await self.embedding_cache.retrieve(gather_text)
            if cached:
                return EmbeddingResponse(embeddings=cached, source="cache")

        # 3. 调用 API
        response = await self.client.embeddings.create(...)
        embeddings = [item.embedding for item in response.data]

        # 4. 存入缓存
        if self.embedding_cache:
            await self.embedding_cache.store(embeddings, gather_text)

        return EmbeddingResponse(embeddings=embeddings, source="api")
```

### 3.6 其他厂商实现概览

| 实现 | 模态 | 厂商 |
|------|------|------|
| `OpenAITextEmbedding` | text | OpenAI |
| `DashScopeTextEmbedding` | text | 阿里 DashScope |
| `DashScopeMultiModalEmbedding` | text + image | 阿里 DashScope |
| `GeminiTextEmbedding` | text | Google Gemini |
| `OllamaTextEmbedding` | text | Ollama（本地模型） |

---

## 4. Token 计数模块源码解读

### 4.1 TokenCounterBase 抽象基类

```python showLineNumbers
class TokenCounterBase:
    @abstractmethod
    async def count(self, messages: list[dict], **kwargs) -> int:
        """计算消息列表的 Token 数量"""
```

**设计简洁**：只有一个 `count()` 方法，输入为格式化后的消息字典列表（由 Formatter 产出），输出为 Token 数量。

### 4.2 CharTokenCounter 字符计数器

```python showLineNumbers
class CharTokenCounter(TokenCounterBase):
    async def count(self, messages: list[dict],
                    tools: list[dict] | None = None) -> int:
        texts = [str(msg) for msg in messages]
        if tools:
            texts.append(str(tools))
        return len("\n".join(texts))
```

**特点**：不依赖任何外部库，直接计算字符长度。适用于快速估算和开发调试。

> **注意**: 不适合多模态数据（base64 编码会显著增加字符数）。

### 4.3 OpenAITokenCounter tiktoken 实现

这是最复杂的 Token 计数器（384 行），包含：

1. **文本 Token 计算**：使用 tiktoken BPE 分词器
2. **图像 Token 计算**：基于 OpenAI 的分块算法
   - 缩放到 2048x2048 范围内
   - 最短边缩放到 768 像素
   - 计算需要的 512px 分块数量
   - Token = 分块数 × tile_tokens + base_tokens
3. **工具定义 Token 计算**：序列化工具 JSON Schema

**图像 Token 算法流程**：
```
原始图像 (4096x2048)
    ↓ 缩放到 2048 范围
2048x1024
    ↓ 最短边到 768
1536x768
    ↓ 计算 512px 分块
3 × 2 = 6 个分块
    ↓ 计算 Token
6 × tile_tokens + base_tokens = 总 Token 数
```

### 4.4 AnthropicTokenCounter API 计数

```python showLineNumbers
class AnthropicTokenCounter(TokenCounterBase):
    def __init__(self, model_name: str, api_key: str):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def count(self, messages: list[dict],
                    tools: list[dict] | None = None) -> int:
        res = await self.client.messages.count_tokens(
            model=self.model_name,
            messages=messages,
            tools=tools,
        )
        return res.input_tokens
```

**特点**：直接调用 Anthropic 的 Token 计数 API，结果精确但每次调用都产生网络请求。

### 4.5 其他计数器概览

| 计数器 | 方法 | 精确度 | 特点 |
|--------|------|--------|------|
| `CharTokenCounter` | 字符计数 | 低 | 零依赖，快速估算 |
| `OpenAITokenCounter` | tiktoken BPE | 高 | 支持图像分块计算，本地执行 |
| `AnthropicTokenCounter` | API 调用 | 最高 | 需要网络请求 |
| `GeminiTokenCounter` | Gemini API | 高 | 需要网络请求 |
| `HuggingFaceTokenCounter` | HuggingFace tokenizer | 高 | 本地执行，需下载 tokenizer |

---

## 5. 设计模式总结

| 设计模式 | 应用位置 | 说明 |
|----------|----------|------|
| **Strategy（策略）** | EmbeddingModelBase / TokenCounterBase | 每个厂商实现一个策略，运行时可替换 |
| **Cache-Aside** | OpenAITextEmbedding + EmbeddingCacheBase | 先查缓存，未命中再调 API 并缓存 |
| **Template Method** | EmbeddingModelBase.__call__ | 基类 `raise NotImplementedError`，子类直接重写 `__call__` 实现 API 交互 |
| **Decorator** | embedding_cache 作为可选装饰 | 缓存作为可选组件注入，不改变核心逻辑 |

---

### 边界情况与陷阱

#### Critical: EmbeddingCache 的 key 冲突

```python showLineNumbers
# EmbeddingCache 使用文本内容作为缓存 key
# 问题：如果两个不同的文本有相同的 hash，会返回错误结果
cache = EmbeddingCache()
result1 = cache.get("Hello world")  # Miss
result2 = cache.get("Hello world")  # Hit
# 但如果文本编码不同（UTF-8 vs GBK），hash 可能不同
```

**解决方案**：使用内容规范化（去除空白、转换为小写等）作为预处理步骤。

#### High: Token 计算的近似性

```python showLineNumbers
# 不同 tokenizer 计算的 token 数量不同
# OpenAI 的 tiktoken vs 实际 API 返回的 token 数

token_count = count_tokens("Hello world")  # tiktoken: 2
# 但实际 API 可能返回不同的数字

# 问题：缓存 key 使用 token count 时可能导致误判
```

#### High: Token 上限与截断

```python showLineNumbers
# 模型有 token 上限（如 4096）
# 超过上限的消息会被截断

messages = [system_msg, history_msgs...]  # 可能超过限制
# 截断策略取决于 Formatter 实现
# 可能截断在句子中间，导致语义丢失
```

#### Medium: 批量嵌入的大小限制

```python showLineNumbers
# OpenAI API 限制单次批量嵌入的文本数量
embedding = OpenAITextEmbedding(...)
results = await embedding([
    "text1", "text2", ..., "text3000"  # 可能超过 API 限制
])
# 需要分批处理
```

#### Medium: 模型参数不一致

```python showLineNumbers
# 不同 embedding 模型使用不同的参数
# DashScope 的 text-embedding-async-v1 vs OpenAI 的 text-embedding-3-small

# 问题：混用配置可能导致不可预期的行为
config = {"model": "text-embedding-3-small", "dimensions": 1024}
# 但 dimensions 参数可能不被某些模型支持
```

---

### 性能考量

#### 嵌入 API 延迟

| 提供商 | 延迟 | 备注 |
|--------|------|------|
| OpenAI | ~500ms | 取决于网络 |
| DashScope | ~200ms | 国内网络更优 |
| Ollama (本地) | ~50ms | 需要本地 GPU |
| HuggingFace | ~100ms | 取决于模型大小 |

#### 缓存命中率影响

```python showLineNumbers
# 缓存命中 vs 未命中的性能差异
cache_hit = ~0.1ms  # 内存查找
cache_miss = ~200ms  # API 调用

# 缓存策略优化：
# - 短文本更容易命中（重复查询）
# - 长文本缓存价值更高（计算成本大）
# - 建议缓存命中率目标：> 70%
```

#### Token 计算性能

```python showLineNumbers
# tiktoken 计算单个字符串：~0.01ms
# 但批量计算时可以使用 C 优化版本

# 对于大量短文本，预计算 token 数可能反而更慢
# 建议：仅在需要精确控制时计算 token 数
```

---

## 6. 代码示例

### 6.1 基本嵌入调用

```python showLineNumbers
from agentscope.embedding import OpenAITextEmbedding

# 创建嵌入模型
embedding_model = OpenAITextEmbedding(
    api_key="sk-...",
    model_name="text-embedding-3-small",
    dimensions=1024,
)

# 调用嵌入
response = await embedding_model(["Hello, world!", "你好，世界！"])
print(f"维度: {len(response.embeddings[0])}")  # 1024
print(f"来源: {response.source}")               # "api"
```

**运行输出**：
```
维度: 1024
来源: api
```

### 6.2 带缓存的嵌入调用

```python showLineNumbers
from agentscope.embedding import OpenAITextEmbedding, FileEmbeddingCache

# 创建带缓存的嵌入模型
cache = FileEmbeddingCache(
    cache_dir="./.cache/my_embeddings",
    max_file_number=1000,      # 最多 1000 个缓存文件
    max_cache_size=500,        # 最大 500MB
)

embedding_model = OpenAITextEmbedding(
    api_key="sk-...",
    model_name="text-embedding-3-small",
    dimensions=1024,
    embedding_cache=cache,     # 注入缓存
)

# 第一次调用：API 请求
r1 = await embedding_model(["What is Python?"])
print(r1.source)  # "api"

# 第二次相同输入：缓存命中
r2 = await embedding_model(["What is Python?"])
print(r2.source)  # "cache"
```

### 6.3 Token 计数用于成本估算

```python showLineNumbers
from agentscope.token import OpenAITokenCounter, CharTokenCounter

# 精确计数
counter = OpenAITokenCounter(model_name="gpt-4")

messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is the meaning of life?"},
]
tokens = await counter.count(messages)
print(f"精确 Token 数: {tokens}")

# 快速估算
rough_counter = CharTokenCounter()
rough_tokens = await rough_counter.count(messages)
print(f"字符数估算: {rough_tokens}")
```

**运行输出**：
```
精确 Token 数: 27
字符数估算: 156
```

---

## 7. 练习题

### 基础题

**Q1**: `EmbeddingResponse` 的 `source` 字段有什么作用？为什么需要区分 "cache" 和 "api"？

**Q2**: 为什么 `CharTokenCounter` 不适合处理包含 base64 图像的消息？

**Q3**: 设计一个场景——你需要对 10,000 条文本进行嵌入，但 API 有速率限制。如何利用 `FileEmbeddingCache` 实现断点续传？

**Q4**: 对比 `OpenAITokenCounter`（本地 tiktoken）和 `AnthropicTokenCounter`（API 调用）的性能和精确度。在什么场景下应该选择哪种？

**Q5**: `FileEmbeddingCache` 使用 SHA256 哈希作为文件名。如果两个不同的文本哈希碰撞，会导致什么问题？如何缓解？

### 中级题

**Q6**: `GeminiTokenCounter` 和 `HuggingFaceTokenCounter` 各有什么优缺点？它们适合在什么场景使用？

**Q7**: 假设你需要处理多模态内容（文本+图像+音频）的 Token 计数。当前只有 `OpenAITokenCounter` 支持图像分块算法。如何设计一个通用的多模态 Token 计数接口？

**Q8**: `EmbeddingCacheBase` 支持 `remove()` 和 `clear()` 方法。这些方法在什么场景下会用到？实现时需要注意哪些并发问题？

### 挑战题

**Q9**: 实现一个 `RedisEmbeddingCache`，使用 Redis 的 Hash 结构存储嵌入向量。需要考虑序列化效率和内存使用。

**Q10**: 实现一个智能缓存预热策略：在批量嵌入前，根据历史访问模式预测可能再次查询的文本，提前进行嵌入并缓存。描述你的预测算法和实现思路。

---

### 参考答案

**A1**: `source` 字段用于追踪数据的来源，便于：(1) 监控缓存命中率，优化缓存策略；(2) 计算实际 API 调用成本（缓存命中不计费）；(3) 调试时区分数据是来自缓存还是实时计算。

**A2**: Base64 编码的图像数据通常包含数十万个字符，而实际图像的 Token 数可能只有几百。字符计数会把 base64 字符串当作普通文本计数，导致严重高估。例如一张 1024x768 的图像，base64 可能产生 500,000+ 字符，但实际可能只消耗约 1,000 Token。

**A3**:
```python showLineNumbers
cache = FileEmbeddingCache(cache_dir="./.cache/batch_embeddings")
embedding_model = OpenAITextEmbedding(
    api_key="sk-...", model_name="text-embedding-3-small",
    embedding_cache=cache,
)

for text in text_list:
    # 缓存会自动跳过已处理的文本
    response = await embedding_model([text])
    if response.source == "api":
        await asyncio.sleep(0.1)  # 速率限制
```
FileEmbeddingCache 会自动持久化，进程重启后已处理的文本直接从缓存返回。

**A4**:
| 维度 | OpenAITokenCounter | AnthropicTokenCounter |
|------|-------------------|---------------------|
| 精确度 | 高（BPE 分词） | 最高（官方 API） |
| 延迟 | 低（本地计算） | 高（网络请求） |
| 成本 | 免费 | 可能计费 |
| 适用场景 | 消息截断、预算估算 | 精确成本核算 |
| 离线支持 | 支持 | 不支持 |

**A5**: 哈希碰撞会导致缓存冲突——错误的嵌入结果被返回给用户。缓解方法：(1) 使用更强的哈希算法（如 SHA-512）；(2) 在缓存命中后验证向量相似度（如余弦相似度低于阈值则视为碰撞）；(3) 添加内容校验，将文本内容的哈希作为二次验证。

**A6**: GeminiTokenCounter 需要网络请求但提供官方精确计数；HuggingFaceTokenCounter 本地执行但需要下载 tokenizer 模型（占用磁盘空间、首次调用慢）。场景选择：需要离线精确计数用 HuggingFace，需要最新模型支持且能接受网络延迟用 Gemini。

**A7**: 关键设计：(1) 定义 `MultiModalTokenCounter` 抽象接口，包含 `count_text()`、`count_image()`、`count_audio()` 等方法；(2) `OpenAITokenCounter` 实现该接口，图像使用分块算法；(3) 其他厂商计数器按需实现各方法；(4) 提供 `count(messages)` 统一入口，自动识别内容类型并路由到对应方法。

**A8**: `remove()` 用于清除特定缓存条目（如数据更新后），`clear()` 用于重置整个缓存。并发问题：(1) 删除操作可能与查询操作冲突，需要加锁；(2) LRU 淘汰和删除操作可能同时修改缓存结构，需要原子操作；(3) 多进程环境下文件缓存的进程间同步问题。

**A9**: 关键实现点：将 `List[float]` 用 NumPy 序列化为 bytes 存入 Redis Hash，identifier 作为 field key。使用 `np.array(embeddings).tobytes()` 序列化，`np.frombuffer(data).tolist()` 反序列化。设置 TTL 自动过期。

**A10**: 关键设计：(1) 访问模式分析——记录每个文本的访问频率和时间间隔，使用 LRU 或时间衰减模型预测；(2) 预热时机——在批量任务开始前或空闲时执行预热；(3) 优先级队列——按预测的再次访问概率排序，优先缓存高概率文本；(4) 缓存容量管理——设置预热缓存上限，避免占用过多空间影响正常缓存。实现可使用简单的指数衰减模型：`probability = base_probability * exp(-decay_rate * time_since_last_access)`。

---

## 设计模式总结

| 设计模式 | 应用位置 | 说明 |
|----------|----------|------|
| **Template Method** | `EmbeddingModelBase.__call__()` / `TokenCounterBase.count()` | 基类定义抽象接口（`raise NotImplementedError`），子类直接重写 `__call__`/`count` 实现具体逻辑 |
| **Cache-Aside** | `OpenAIEmbedding.__call__()` | 先查缓存命中则直接返回，未命中时调用 API 并写入缓存，解耦缓存与数据源 |
| **Strategy** | `TokenCounterBase` 多实现 | CharTokenCounter / OpenAITokenCounter / AnthropicTokenCounter 可互换，Formatter 通过抽象接口选择策略 |
| **Flyweight** | `FileEmbeddingCache` | 相同文本的嵌入向量只存储一份，通过 identifier 复用，避免重复计算 |
| **Adapter** | `AnthropicTokenCounter` | 适配 Anthropic 非 streaming API 进行 token 计数，对上层提供统一的 `count()` 接口 |

---

## 模块小结

| 概念 | 要点 |
|------|------|
| EmbeddingModelBase | 嵌入模型抽象，通过 `__call__` 调用 |
| EmbeddingCacheBase | 缓存抽象，Cache-Aside 模式 |
| FileEmbeddingCache | 基于文件的缓存，支持 LRU 淘汰 |
| TokenCounterBase | Token 计数抽象，`count()` 方法 |
| CharTokenCounter | 字符级估算，零依赖 |
| OpenAITokenCounter | tiktoken 精确计数，含图像分块算法 |

| 关联模块 | 关联点 | 参考位置 |
|----------|--------|----------|
| [记忆与 RAG 模块](module_memory_rag_deep.md#5-rag-模块架构) | RAG 检索依赖 Embedding 进行向量化 | 第 5.4 节 |
| [模型模块](module_model_deep.md#8-token-计数机制) | Token 计数用于模型成本控制 | 第 8.1 节 |
| [格式化器模块](module_formatter_deep.md#32-truncatedformatterbase-截断格式化器) | TruncatedFormatterBase 使用 TokenCounter 截断消息 | 第 3.2 节 |
| [追踪模块](module_tracing_deep.md#3-追踪装饰器) | trace_embedding() 追踪嵌入 API 调用 | 第 3.5 节 |
| [状态模块](module_state_deep.md#3-源码解读) | 嵌入缓存可结合状态管理实现持久化 | 第 3.3 节 |


---

## 本章小结

- EmbeddingModelBase 定义嵌入模型抽象接口，通过 `__call__` 统一调用
- FileEmbeddingCache 采用 Cache-Aside 模式和 LRU 淘汰策略，相同文本只存储一份嵌入向量
- TokenCounterBase 提供多种实现：CharTokenCounter（字符估算、零依赖）、OpenAITokenCounter（tiktoken 精确计数含图像分块）、AnthropicTokenCounter（API 计数）
- Embedding 模块与 RAG 检索紧密集成，Token 模块为 Formatter 和成本控制提供基础
- 不同 Token 计数策略在精确度与延迟之间取舍：精确 API 调用 vs 本地估算

## 下一章

→ [Plan 规划模块](module_plan_deep.md)
