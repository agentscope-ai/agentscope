# -*- coding: utf-8 -*-
"""The main entry point of the RAG example."""
import asyncio

from agentscope.embedding import DashScopeTextEmbedding
from agentscope.rag import QdrantStore, KnowledgeBase, TextReader, PDFReader

async def main() -> None:
    """The main entry point of the RAG example."""

    # 数据划分，分块
    reader = TextReader(chunk_size=1024, chunk_overlap=100)
    pdf_reader = PDFReader(chunk_size=1024, chunk_overlap=100)

    documents = reader(text="我是高大伟, 我的账号密码是123456")
    pdf_documents = pdf_reader(file_path="path/to/your/file.pdf")

    # 创建知识库
    knowledge = KnowledgeBase(
        embedding_store=QdrantStore(
            collection_name="test_collection",
            host="localhost",
            port=6333,
        ),
        embedding_model=DashScopeTextEmbedding(
            api_key="your-dashscope-api-key",
            model_name="text-embedding-3-small",
        ),
    )
    # 插入知识
    await knowledge.upsert([*documents, *pdf_documents])

    # 检索知识
    res = await knowledge.retrieve(query="高大伟的账号密码是多少？")


asyncio.run(main())
