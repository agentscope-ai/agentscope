# -*- coding: utf-8 -*-
"""
.. _embedding:

嵌入(Embedding)
=========================

AgentScope 中，嵌入模块提供了用于向量生成的统一接口，具有以下特性：

- 支持 **缓存 embedding** 以避免冗余的 API 调用
- 支持 **不同 embedding API 提供商** 并提供一致的接口

AgentScope 内置支持以下 API：

.. list-table::
    :header-rows: 1

    * - API 提供商
      - 类
    * - OpenAI
      - ``OpenAITextEmbedding``
    * - Gemini
      - ``GeminiTextEmbedding``
    * - DashScope
      - ``DashScopeTextEmbedding``, ``DashScopeMultiModalEmbedding``
    * - Ollama
      - ``OllamaTextEmbedding``

所有类都继承自 ``EmbeddingModelBase``，实现了 ``__call__`` 方法并生成包含嵌入和使用信息的 ``EmbeddingResponse`` 对象。
其中 ``DashScopeMultiModalEmbedding`` 支持文本，图像和视频的多模态嵌入。

以 DashScope 嵌入类为例，可以按如下方式使用：
"""

import asyncio
import os
import tempfile

from agentscope.embedding import DashScopeTextEmbedding, FileEmbeddingCache


async def example_dashscope_embedding() -> None:
    """DashScope 文本嵌入的使用示例。"""
    texts = [
        "法国的首都是什么？",
        "巴黎是法国的首都城市。",
    ]

    # 初始化 DashScope 文本嵌入实例
    embedding_model = DashScopeTextEmbedding(
        model_name="text-embedding-v2",
        api_key=os.getenv("DASHSCOPE_API_KEY"),
    )

    # 从模型获取嵌入
    response = await embedding_model(texts)

    print("嵌入 ID: ", response.id)
    print("嵌入创建时间: ", response.created_at)
    print("嵌入使用情况: ", response.usage)
    print("嵌入向量:")
    print(response.embeddings)


asyncio.run(example_dashscope_embedding())

# %%
# 可以通过继承 ``EmbeddingModelBase`` 并实现 ``__call__`` 方法来自定义 embedding 模型。
#
# Embedding 缓存
# ---------------------
# AgentScope 提供了用于缓存 embedding 的基类 ``EmbeddingCacheBase``，以及基于文件的实现 ``FileEmbeddingCache``。
# 它在 embedding 模块中的工作方式如下：
#
# .. image:: ../../_static/images/embedding_cache.png
#   :align: center
#   :width: 90%
#
# 要使用缓存，只需将 ``FileEmbeddingCache`` 实例（或自定义缓存）传给模型的构造函数，如下所示：
#


async def example_embedding_cache() -> None:
    """演示带有缓存功能的 embedding。"""
    # 示例文本
    texts = [
        "法国的首都是什么？",
        "巴黎是法国的首都城市。",
    ]

    # 为缓存演示创建临时目录
    # 在实际应用中，建议使用持久目录以最大发挥缓存效果
    cache_dir = tempfile.mkdtemp(prefix="embedding_cache_")
    print(f"使用缓存目录: {cache_dir}")

    # 使用缓存初始化嵌入模型
    # 为演示目的，我们将缓存限制为 100 个文件和 10MB
    embedder = DashScopeTextEmbedding(
        model_name="text-embedding-v3",
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        embedding_cache=FileEmbeddingCache(
            cache_dir=cache_dir,
            max_file_number=100,
            max_cache_size=10,  # 最大缓存大小（MB）
        ),
    )

    # 第一次调用 - 将从 API 获取并存储在缓存中
    print("\n=== 第一次 API 调用（无缓存命中）===")
    start_time = asyncio.get_event_loop().time()
    response1 = await embedder(texts)
    elapsed_time1 = asyncio.get_event_loop().time() - start_time
    print(f"来源: {response1.source}")  # 应该是 'api'
    print(f"耗时: {elapsed_time1:.4f} 秒")
    print(f"使用的 token: {response1.usage.tokens}")

    # 使用相同文本的第二次调用 - 应该使用缓存
    print("\n=== 第二次 API 调用（预期缓存命中）===")
    start_time = asyncio.get_event_loop().time()
    response2 = await embedder(texts)
    elapsed_time2 = asyncio.get_event_loop().time() - start_time
    print(f"来源: {response2.source}")  # 应该是 'cache'
    print(f"耗时: {elapsed_time2:.4f} 秒")
    print(
        f"使用的 token: {response2.usage.tokens}",
    )  # 缓存结果应该为 0
    print(
        f"速度提升: 使用缓存快 {elapsed_time1 / elapsed_time2:.1f} 倍",
    )


asyncio.run(example_embedding_cache())
