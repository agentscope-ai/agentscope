# -*- coding: utf-8 -*-
"""The example of integrating ReAct agent with RAG."""
import asyncio
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.rag import SimpleKnowledge, QdrantStore, TextReader


async def main() -> None:
    """The main entry point for the ReAct agent with RAG example."""

    # Create an in-memory knowledge base instance
    print("Creating the knowledge base...")
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

    # Insert some documents into the knowledge base
    # This could be done offline and only once
    print("Inserting documents into the knowledge base...")
    reader = TextReader(chunk_size=100, split_by="char")
    documents = await reader(
        # Fake personal profile for demonstration
        "I'm John Doe, 28 years old. My best friend is James "
        "Smith. I live in San Francisco. I work at OpenAI as a "
        "software engineer. I love hiking and photography. "
        "My father is Michael Doe, a doctor. I'm very proud of him. "
        "My mother is Sarah Doe, a teacher. She is very kind and "
        "always helps me with my studies.\n"
        "I'm now a PhD student at Stanford University, majoring in "
        "Computer Science. My advisor is Prof. Jane Williams, who is "
        "a leading expert in artificial intelligence. I have published "
        "several papers in top conferences, such as NeurIPS and ICML. ",
    )

    print("Inserting documents into the knowledge base...")
    await knowledge.add_documents(documents)

    # Integrate into the ReActAgent by the `knowledge` argument
    print("Creating the agent...")
    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="qwen-max",
        ),
        formatter=DashScopeChatFormatter(),
        # Equip the agent with the knowledge base
        knowledge=knowledge,
        print_hint_msg=True,
    )
    user = UserAgent(name="user")

    # Start the conversation
    print("Start the conversation...")
    msg = Msg("user", "Do you know who is my best friend?", "user")
    while True:
        msg = await agent(msg)
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break


asyncio.run(main())
