# -*- coding: utf-8 -*-
"""Memory example demonstrating long-term memory functionality with mem0.

This module provides examples of how to use the Mem0LongTermMemory class
for recording and retrieving persistent memories.
"""

import asyncio
import os

from dotenv import load_dotenv
from mem0.vector_stores.configs import VectorStoreConfig

from agentscope.memory import Mem0LongTermMemory
from agentscope.agent import ReActAgent
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit


load_dotenv()


async def main() -> None:
    """Run the memory examples."""
    # Initialize long term memory
    long_term_memory = Mem0LongTermMemory(
        agent_name="Friday",
        user_name="user_123",
        model=DashScopeChatModel(
            model_name="qwen-max-latest",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            stream=False,
        ),
        embedding_model=DashScopeTextEmbedding(
            model_name="text-embedding-v3",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            dimensions=1024,
        ),
        vector_store_config=VectorStoreConfig(
            provider="qdrant",
            config={
                "on_disk": True,
                "path": "../memory/qdrant_data",  # Specify custom path
                "embedding_model_dims": 1024,
            },
        ),
    )

    print("=== Long Term Memory Examples with mem0 ===\n")

    # Example 1: Basic conversation recording
    print("1. Basic Conversation Recording")
    print("-" * 40)
    results = await long_term_memory.record(
        msgs=[
            Msg(
                role="user",
                content="Please help me book a hotel, preferably homestay",
                name="user",
            ),
        ],
    )
    print(f"Recorded conversation: {results}\n")

    # Example 2: Retrieving memories
    print("2. Retrieving Memories")
    print("-" * 40)
    print("Searching for weather-related memories...")
    weather_memories = await long_term_memory.retrieve(
        msg=[
            Msg(
                role="user",
                content="What's the weather like today?",
                name="user",
            ),
        ],
    )
    print(f"Retrieved weather memories: {weather_memories}\n")

    print("Searching for user preference memories...")
    preference_memories = await long_term_memory.retrieve(
        msg=[
            Msg(
                role="user",
                content=(
                    "I prefer temperatures in Celsius and wind speed in km/h"
                ),
                name="user",
            ),
        ],
    )
    print(f"Retrieved preference memories: {preference_memories}\n")

    # Example 5: ReActAgent with long term memory
    print("5. ReActAgent with long term memory")
    print("-" * 40)

    toolkit = Toolkit()
    agent = ReActAgent(
        name="Friday",
        sys_prompt=(
            "You are a helpful assistant named Friday. "
            "If you think there is relevant information about "
            "user's preference, you can record it to the long term "
            "memory by tool call `record_to_memory`. "
            "If you need to retrieve information from the long term "
            "memory, you can use the tool call `retrieve_from_memory`."
        ),
        model=DashScopeChatModel(
            model_name="qwen-max-latest",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            stream=False,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        long_term_memory=long_term_memory,
        long_term_memory_mode="both",
    )

    await agent.memory.clear()
    msg = Msg(
        role="user",
        content="When I travel to Hangzhou, I prefer to stay in a homestay",
        name="user",
    )
    msg = await agent(msg)
    print(f"ReActAgent response: {msg.get_text_content()}\n")

    msg = Msg(role="user", content="what preference do I have?", name="user")
    msg = await agent(msg)
    print(f"ReActAgent response: {msg.get_text_content()}\n")
    msg = Msg(
        role="user",
        content="I prefer to visit the West Lake",
        name="user",
    )
    msg = await agent(msg)
    print(f"ReActAgent response: {msg.get_text_content()}\n")


if __name__ == "__main__":
    asyncio.run(main())
