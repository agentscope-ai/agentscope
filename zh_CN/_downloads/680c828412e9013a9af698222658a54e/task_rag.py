# -*- coding: utf-8 -*-
"""
.. _rag:

RAG
==========================

AgentScope 提供了内置的 RAG（Retrieval-Augmented Generation) 实现。本节将详细介绍

- 如何使用 AgentScope 中的 RAG 模块，
- 如何实现 **多模态** RAG，
- 如何在 ``ReActAgent`` 中以两种不同的方式集成 RAG 模块：
    - 智能体自主控制（Agentic manner）
    - 通用方式（Generic manner）

我们在下列表格中总结了两种模式的优缺点：

.. list-table:: RAG 集成方式比较
    :header-rows: 1

    * - 集成方式
      - 描述
      - 优点
      - 缺点
    * - 智能体自主控制
      - 以工具调用方式让智能体自主决定何时进行查询，查询什么关键字
      - - 与 ReAct 算法契合，灵活性高
        - 智能体能够根据当前的上下文改写查询关键词
        - 避免在不必要时发生查询
      - 对 LLM 模型能力要求较高
    * - 通用方式
      - 每次在 ``reply`` 函数开始时固定进行查询，并将检索结果整合到提示（prompt）中
      - - 实现简单
        - 对 LLM 模型能力要求低
      - - 每次都会运行查询，因此会引入过多不必要的查询检索
        - 查询数据库较大时，回复延迟较高

.. note:: 作为开源框架，AgentScope 的目标是让开发过程更简单也更有趣。因此，AgentScope 的设计中并不强制要求使用内置的 RAG 实现，同时支持、鼓励开发者集成现有的 RAG 实现或第三方 RAG 框架。

"""
import asyncio
import json
import os

from matplotlib import pyplot as plt

import agentscope
from agentscope.agent import ReActAgent
from agentscope.embedding import (
    DashScopeTextEmbedding,
    DashScopeMultiModalEmbedding,
)
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.rag import (
    TextReader,
    SimpleKnowledge,
    QdrantStore,
    Document,
    ImageReader,
)
from agentscope.tool import Toolkit

# %%
# 使用 RAG 模块
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope 中的 RAG 模块由以下三个核心组件构成：
#
# - **Reader**：负责从数据源读取数据，并进行分块（chunking）
# - **Knowledge**：负责知识库查询检索和数据存储逻辑的算法实现
# - **Store**：负责与向量数据库交互的逻辑实现
#
# .. note:: 我们将持续在 AgentScope 中集成新的向量数据库和数据读取模块。详情请查看我们的 `开发路线图 <https://github.com/orgs/agentscope-ai/projects/2>`_，同时也欢迎贡献代码！
#
# 当前 AgentScope 中内置支持的 reader 包括：
#

for _ in agentscope.rag.__all__:
    if _.endswith("Reader"):
        print(f"- {_}")

# %%
# 这些 reader 的作用是读取数据，将数据分块并包装成 ``agentscope.rag.Document`` 对象。 ``Document`` 对象包含以下字段：
#
# - ``metadata``：数据块的元信息，包含数据内容（``content``）、数据 ID（``doc_id``）、块 ID（``chunk_id``）和总块数（``total_chunks``）
# - ``embedding``: 数据块的向量表示，默认为 ``None``，在将数据块添加到知识库时会被填充
# - ``score``: 数据块的相关性分数，默认为 ``None``，在从知识库检索数据块时会被填充
#
# 以 ``TextReader`` 为例，通过如下代码读取文本字符串，并将文本分块为 ``Document`` 对象：
#


async def example_text_reader(print_docs: bool) -> list[Document]:
    """使用 TextReader 读取文本字符串，并将文本分块为 Document 对象。"""
    # 创建 TextReader 对象
    reader = TextReader(chunk_size=512, split_by="paragraph")

    # 读取文本字符串
    documents = await reader(
        text=(
            # 我们准备一些文本数据用于演示 RAG 功能。
            "我的名字是李明，今年28岁。\n"
            "我居住在中国杭州，是一名算法工程师。我喜欢打篮球和玩游戏。\n"
            "我父亲的名字是李强，是一名医生，我的母亲是陈芳芳，是一名教师，她总是指导我学习。\n"
            "我现在在北京大学攻读博士学位，研究方向是人工智能。\n"
            "我最好的朋友是王伟，我们从小一起长大，现在他是一名律师。"
        ),
    )

    if print_docs:
        print(f"文本被分块为 {len(documents)} 个 Document 对象：")
        for idx, doc in enumerate(documents):
            print(f"Document {idx}:")
            print("\tScore: ", doc.score)
            print(
                "\tMetadata: ",
                json.dumps(doc.metadata, indent=2, ensure_ascii=False),
                "\n",
            )

    return documents


docs = asyncio.run(example_text_reader(print_docs=True))

# %%
# 由于并不存在一个 “one-for-all” 的数据读取和分块方法，特别像是 PDF 和 Word 这类复杂格式的文档。
# 因此，AgentScope 鼓励开发者根据自己的数据格式实现自定义的 reader。
# 只需要继承 ``BaseReader`` 类，并实现 ``__call__`` 方法即可。
#
# 在数据分块后，接下来需要将数据块添加到知识库中。
# 在 AgentScope 中，知识库的初始化需要提供 **嵌入模型** 和 **向量存储** （即向量数据库） 的对象。
# AgentScope 目前内置支持基于 `Qdrant <https://qdrant.tech/>`_ 实现的向量存储，以及一个知识库的基础实现 ``SimpleKnowledge``。
# 具体使用方式如下：
#
# .. note::
#
#  - 我们正在 AgentScope 中集成新的向量数据库，详情请查看我们的 `开发路线图 <https://github.com/orgs/agentscope-ai/projects/2>`_。欢迎贡献代码！
#  - Qdrant 的实现通过 ``location`` 参数支持多种不同的部署方式，包括内存模式，本地模式和云端模式。详情请参考 `Qdrant 文档 <https://qdrant.tech/>`_。
#


async def build_knowledge_base() -> SimpleKnowledge:
    """构建知识库。"""
    # 读取 documents 数据
    documents = await example_text_reader(print_docs=False)

    # 创建一个内存中的 Qdrant 向量存储，以及使用 DashScopeTextEmbedding 作为嵌入模型，初始化知识库
    knowledge = SimpleKnowledge(
        # 提供一个 embedding 模型用于将文本转换为向量
        embedding_model=DashScopeTextEmbedding(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="text-embedding-v4",
            dimensions=1024,
        ),
        # 选择 Qdrant 作为向量存储
        embedding_store=QdrantStore(
            location=":memory:",  # 使用内存模式
            collection_name="test_collection",
            dimensions=1024,  # 嵌入向量的维度必须与嵌入模型输出的维度一致
        ),
    )

    # 将 documents 添加到知识库中
    await knowledge.add_documents(documents)

    # 从知识库中检索数据
    docs = await knowledge.retrieve(
        query="李明的父亲是谁？",
        limit=3,
        score_threshold=0.5,
    )

    print("检索到的 Document 对象：")
    for doc in docs:
        print(doc, "\n")

    return knowledge


knowledge = asyncio.run(build_knowledge_base())

# %%
# AgentScope 中的知识库类提供两个核心方法：``add_documents`` 和 ``retrieve``，分别用于添加数据块和搜索检索数据块。
#
# 此外，AgentScope 提供了 ``retrieve_knowledge`` 方法，它将 ``retrieve`` 方法封装成一个智能体能够直接调用的工具函数。开发者可以直接使用该工具函数装备智能体。
#
# 自定义 RAG 组件
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope 支持并鼓励开发者自定义 RAG 组件，包括 reader、知识库和向量数据库。
# 具体来说，我们提供了以下基类用于自定义：
#
# .. list-table:: RAG 基类
#     :header-rows: 1
#
#     * - 基类
#       - 描述
#       - 抽象方法
#     * - ``ReaderBase``
#       - 所有 reader 的基类
#       - ``__call__``
#     * - ``VDBStoreBase``
#       - 向量数据库的基类
#       - | ``add``
#         | ``search``
#         | ``get_client`` (可选)
#         | ``delete`` (可选)
#     * - ``KnowledgeBase``
#       - 知识库的基类
#       - | ``retrieve``
#         | ``add_documents``
#
# ``VDBStoreBase`` 中的 ``get_client`` 方法允许开发者访问底层向量数据库的完整功能。
# 这样，他们就可以基于向量数据库实现更高级的功能，例如建立索引、高级搜索等。
#
# 与 ReActAgent 集成
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# 接下来，我们将演示如何以智能体自主控制（agentic）和通用（generic）两种方式将 RAG 模块与 AgentScope 中的 ``ReActAgent`` 集成。
#
# 智能体自主控制
# --------------------------------
# 在智能体自主控制的方式中，ReAct 智能体可以自主决定何时检索知识以及检索的查询内容。
# 将 RAG 模块与 AgentScope 中的 ``ReActAgent`` 集成非常简单，只需将知识库的 ``retrieve_knowledge`` 方法注册为工具，并为该工具提供适当的描述即可。


async def example_agentic_manner() -> None:
    """以智能体自主控制方式将 RAG 模块与 ReActAgent 集成的示例。"""
    # 创建一个 ReAct 智能体
    toolkit = Toolkit()

    # 使用 DashScope 作为模型创建 ReAct 智能体
    agent = ReActAgent(
        name="Friday",
        sys_prompt="You're a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="qwen-max",
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
    )

    print("第一次回复: ")
    # 第一次我们进行一些交流，提供“李明”这个名字作为上下文内容
    await agent(
        Msg(
            "user",
            "李明是我最好的朋友。",
            "user",
        ),
    )

    # 将 retrieve_knowledge 方法注册为工具箱中的工具函数
    toolkit.register_tool_function(
        knowledge.retrieve_knowledge,
        func_description=(  # 为工具提供清晰的描述
            "用于检索与给定查询相关的文档的工具。" "当你需要查找有关李明的信息时使用此工具。"
        ),
    )

    print("\n\n第二次回复: ")
    # 第二次回复中，我们希望智能体能够将查询中“他父亲”改写得更具体，例如
    # “李明的父亲是谁？”或“李明的父亲”
    await agent(
        Msg(
            "user",
            "你知道他父亲是谁吗？",
            "user",
        ),
    )


asyncio.run(example_agentic_manner())

# %%
# 在上面的例子中，我们模拟了正常与智能体交流过程。第一次的交流我们提供了“李明”的名字作为上下文内容。
# 第二次提问时，我们的问题是“你知道他父亲是谁吗？”，
# 我们希望智能体能够利用上下文历史信息改写查询，使其更具体，更好的进行检索，例如改写为“李明的父亲是谁？”或“李明的父亲”。
#
# 更进一步，结合 :ref:`plan` 模块，我们可以让智能体实现更加复杂的查询改写和多轮检索。
#
# 通用方式
# --------------------------------
# ``ReActAgent`` 还以一种更加通用的方式集成了 RAG 模块，
# 它在每次 ``reply`` 函数开始执行时检索知识，并将检索到的知识附加到用户消息的提示中。
#
# 只需设置 ``ReActAgent`` 的 ``knowledge`` 参数，智能体就会在每次回复开始时自动检索知识。
#


async def example_generic_manner() -> None:
    """以通用方式将 RAG 模块与 ReActAgent 集成的示例。"""
    # 创建一个 ReAct 智能体
    agent = ReActAgent(
        name="Friday",
        sys_prompt="You're a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="qwen-max",
        ),
        formatter=DashScopeChatFormatter(),
        # 将知识库传递给智能体
        knowledge=knowledge,
    )

    await agent(
        Msg(
            "user",
            "你知道李明的父亲是谁吗？",
            "user",
        ),
    )

    print("\n查看智能体记忆中检索信息如何插入：")
    content = (await agent.memory.get_memory())[1].content
    print(json.dumps(content, indent=2, ensure_ascii=False))


asyncio.run(example_generic_manner())


# %%
# 多模态 RAG
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope 中的 RAG 模块原生支持多模态，因为
#
# - AgentScope 支持多模态嵌入 API，例如 ``DashScopeMultimodalEmbedding``。
# - ``Document`` 类的 ``metadata`` 中，``content`` 字段的类型是 ``TextBlock | ImageBlock | VideoBlock``，因此可以存储文本、图片和视频等多模态数据。
#
# 因此，我们可以直接使用多模态 reader 和嵌入模型来构建多模态知识库，如下所示。
#
# 首先，我们准备一张本地的图片，这张图片上包含了文本“My name is Ming Li”。

# 准备一张包含文本“My name is Ming Li”的图片。
path_image = "./example.png"
plt.figure(figsize=(8, 3))
plt.text(0.5, 0.5, "My name is Ming Li", ha="center", va="center", fontsize=30)
plt.axis("off")
plt.savefig(path_image, bbox_inches="tight", pad_inches=0.1)
plt.close()

# %%
# 然后我们可以构建一个多模态知识库，构建过程与纯文本知识库类似。只是将 reader 和嵌入模型替换为多模态版本即可。
# 在下面的例子中，我们使用了 ``ImageReader`` 和 ``DashScopeMultiModalEmbedding``。
# 同时，这里我们使用多模态模型 ``qwen3-vl-plus`` 作为智能体的语言模型。
#


async def example_multimodal_rag() -> None:
    """使用多模态 RAG 的示例。"""
    # 使用 ImageReader 读取图片
    reader = ImageReader()
    docs = await reader(image_url=path_image)

    # 创建一个知识库
    knowledge = SimpleKnowledge(
        embedding_model=DashScopeMultiModalEmbedding(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="multimodal-embedding-v1",
            dimensions=1024,
        ),
        embedding_store=QdrantStore(
            location=":memory:",
            collection_name="test_collection",
            dimensions=1024,
        ),
    )

    await knowledge.add_documents(docs)

    agent = ReActAgent(
        name="Friday",
        sys_prompt="You're a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="qwen3-vl-plus",
        ),
        formatter=DashScopeChatFormatter(),
        knowledge=knowledge,
    )

    await agent(
        Msg(
            "user",
            "你知道我的名字吗？",
            "user",
        ),
    )

    # 让我们看看检索到的图片数据是否已经加入了智能体的记忆中
    print("\n查看智能体记忆中检索信息如何插入：")
    content = (await agent.memory.get_memory())[1].content
    print(json.dumps(content, indent=2, ensure_ascii=False))


asyncio.run(example_multimodal_rag())

# %%
# 进一步阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`embedding`
# - :ref:`plan`
#
