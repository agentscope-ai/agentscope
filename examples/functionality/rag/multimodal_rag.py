# -*- coding: utf-8 -*-
"""The example of how to use multimodal RAG in AgentScope"""
import asyncio
import json
import os

from matplotlib import pyplot as plt

from agentscope.agent import ReActAgent
from agentscope.embedding import DashScopeMultiModalEmbedding
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.rag import ImageReader, SimpleKnowledge, QdrantStore


path_image = "./example.png"
plt.figure(figsize=(8, 3))
plt.text(0.5, 0.5, "My name is Ming Li", ha="center", va="center", fontsize=30)
plt.axis("off")
plt.savefig(path_image, bbox_inches="tight", pad_inches=0.1)
plt.close()


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
    content = (await agent.memory.get_memory())[-4].content
    print(json.dumps(content, indent=2, ensure_ascii=False))


asyncio.run(example_multimodal_rag())
