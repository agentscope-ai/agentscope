# -*- coding: utf-8 -*-
"""The main entry point of the RAG example."""
import asyncio
import os

from agentscope.embedding import DashScopeTextEmbedding
from agentscope.rag import (
    TextReader,
    PDFReader,
    QdrantStore,
    SimpleKnowledge,
)


async def main() -> None:
    """The main entry point of the RAG example."""

    # Create readers with chunking arguments
    reader = TextReader(chunk_size=1024)
    pdf_reader = PDFReader(chunk_size=1024, split_by="sentence")

    # Read documents
    documents = await reader(
        text="I'm Tony Stank, my password is 123456. My best friend is James "
        "Rhodes.",
    )

    # Read a sample PDF file
    pdf_path = os.path.join(
        os.path.abspath(os.path.dirname(__file__)),
        "example.pdf",
    )
    pdf_documents = await pdf_reader(pdf_path=pdf_path)

    # Create a knowledge base with Qdrant as the embedding store and
    # DashScope as the embedding model
    knowledge = SimpleKnowledge(
        embedding_store=QdrantStore(
            location=":memory:",
            collection_name="test_collection",
            dimensions=1024,  # The dimension of the embedding vectors
        ),
        embedding_model=DashScopeTextEmbedding(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="text-embedding-v4",
        ),
    )

    # Insert documents into the knowledge base
    await knowledge.add_documents(documents + pdf_documents)

    # Retrieve relevant documents based on a given query
    docs = await knowledge.retrieve(
        query="What is Tony Stank's password?",
        limit=3,
        score_threshold=0.7,
    )
    print("Q1: What is Tony Stank's password?")
    for doc in docs:
        print(
            f"Document ID: {doc.id}, Score: {doc.score}, "
            f"Content: {doc.metadata.content['text']}",
        )

    # Retrieve documents from the PDF file based on a query
    docs = await knowledge.retrieve(
        query="climate change",
        limit=3,
        score_threshold=0.2,
    )
    print("\n\nQ2: climate change")
    for doc in docs:
        print(
            f"Document ID: {doc.id}, Score: {doc.score}, "
            f"Content: {repr(doc.metadata.content['text'])}",
        )


asyncio.run(main())
