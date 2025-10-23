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
    """Example for multimodal RAG"""
    # Reading the image and converting it to documents
    reader = ImageReader()
    docs = await reader(image_url=path_image)

    # Create a knowledge base and add documents
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
            "Do you know my name?",
            "user",
        ),
    )

    # Let's see if the agent has stored the retrieved document in its memory
    print("\nThe retrieved document stored in the agent's memory:")
    content = (await agent.memory.get_memory())[-4].content
    print(json.dumps(content, indent=2, ensure_ascii=False))


asyncio.run(example_multimodal_rag())
