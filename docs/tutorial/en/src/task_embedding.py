# -*- coding: utf-8 -*-
"""
.. _embedding:

Embedding
=========================

In AgentScope, the embedding module provides a unified interface for vector representation generation, which features:

- Support **caching embeddings** to avoid redundant API calls
- Support **multiple embedding providers** with a consistent API

AgentScope has built-in embedding classes for the following API providers:

.. list-table::
    :header-rows: 1

    * - Provider
      - Class
    * - OpenAI
      - ``OpenAITextEmbedding``
    * - Gemini
      - ``GeminiTextEmbedding``
    * - DashScope
      - ``DashScopeTextEmbedding``, ``DashScopeMultiModalEmbedding``
    * - Ollama
      - ``OllamaTextEmbedding``

All classes inherit from ``EmbeddingModelBase``, implementing the ``__call__`` method and generating ``EmbeddingResponse`` object with the embeddings and usage information.
The ``DashScopeMultiModalEmbedding`` supports multi-modal embeddings for text, images, and videos.

Taking the DashScope embedding class as an example, you can use it as follows:
"""

import asyncio
import os
import tempfile

from agentscope.embedding import DashScopeTextEmbedding, FileEmbeddingCache


async def example_dashscope_embedding() -> None:
    """Example usage of DashScope text embedding."""
    texts = [
        "What is the capital of France?",
        "Paris is the capital city of France.",
    ]

    # Initialize the DashScope text embedding instance
    embedding_model = DashScopeTextEmbedding(
        model_name="text-embedding-v2",
        api_key=os.getenv("DASHSCOPE_API_KEY"),
    )

    # Get the embedding from the model
    response = await embedding_model(texts)

    print("The embedding ID: ", response.id)
    print("The embedding create at: ", response.created_at)
    print("The embedding usage: ", response.usage)
    print("The embedding:")
    print(response.embeddings)


asyncio.run(example_dashscope_embedding())

# %%
# You can customize your embedding model by subclassing ``EmbeddingModelBase`` and implementing the ``__call__`` method.
#
# Embedding Cache
# ---------------------
# AgentScope provides a base class ``EmbeddingCacheBase`` for caching embeddings, as well as a file-based implementation ``FileEmbeddingCache``.
# It works as follows in the embedding module:
#
# .. image:: ../../_static/images/embedding_cache.png
#   :align: center
#   :width: 90%
#
# To use caching, just pass an instance of ``FileEmbeddingCache`` (or your custom cache) to the embedding model's constructor as follows:
#


async def example_embedding_cache() -> None:
    """Demonstrate embedding with cache functionality."""
    # Example texts
    texts = [
        "What is the capital of France?",
        "Paris is the capital city of France.",
    ]

    # Create a temporary directory for cache demonstration
    # In real applications, you might want to use a persistent directory
    cache_dir = tempfile.mkdtemp(prefix="embedding_cache_")
    print(f"Using cache directory: {cache_dir}")

    # Initialize the embedding model with cache
    # We limit the cache to 100 files and 10MB for demonstration purposes
    embedder = DashScopeTextEmbedding(
        model_name="text-embedding-v3",
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        embedding_cache=FileEmbeddingCache(
            cache_dir=cache_dir,
            max_file_number=100,
            max_cache_size=10,  # Maximum cache size in MB
        ),
    )

    # First call - will fetch from API and store in cache
    print("\n=== First API Call (No Cache Hit) ===")
    start_time = asyncio.get_event_loop().time()
    response1 = await embedder(texts)
    elapsed_time1 = asyncio.get_event_loop().time() - start_time
    print(f"Source: {response1.source}")  # Should be 'api'
    print(f"Time taken: {elapsed_time1:.4f} seconds")
    print(f"Tokens used: {response1.usage.tokens}")

    # Second call with the same texts - should use cache
    print("\n=== Second API Call (Cache Hit Expected) ===")
    start_time = asyncio.get_event_loop().time()
    response2 = await embedder(texts)
    elapsed_time2 = asyncio.get_event_loop().time() - start_time
    print(f"Source: {response2.source}")  # Should be 'cache'
    print(f"Time taken: {elapsed_time2:.4f} seconds")
    print(
        f"Tokens used: {response2.usage.tokens}",
    )  # Should be 0 for cached results
    print(
        f"Speed improvement: {elapsed_time1 / elapsed_time2:.1f}x faster with cache",
    )


asyncio.run(example_embedding_cache())
