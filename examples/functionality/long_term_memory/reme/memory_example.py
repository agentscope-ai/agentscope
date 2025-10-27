# -*- coding: utf-8 -*-
"""Memory example demonstrating long-term memory functionality with ReMe.

This module provides examples of how to use the RemeLongTermMemory class
for recording and retrieving persistent memories.
"""

import asyncio
import os

from dotenv import load_dotenv

from agentscope.memory import RemeLongTermMemory
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
    # Initialize long term memory with ReMe
    async with RemeLongTermMemory(
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
    ) as long_term_memory:

        print("=== Long Term Memory Examples with ReMe ===\n")

        # Example 1: Basic conversation recording
        print("1. Basic Conversation Recording")
        print("-" * 40)
        await long_term_memory.record(
            msgs=[
                Msg(
                    role="user",
                    content="Please help me book a hotel, preferably homestay",
                    name="user",
                ),
            ],
        )
        print("Recorded conversation\n")

        # Example 2: Retrieving memories
        print("2. Retrieving Memories")
        print("-" * 40)
        print("Searching for hotel-related memories...")
        hotel_memories = await long_term_memory.retrieve(
            msg=[
                Msg(
                    role="user",
                    content="What's my hotel preference?",
                    name="user",
                ),
            ],
        )
        print(f"Retrieved hotel memories: {hotel_memories}\n")

        # Example 3: ReActAgent with long term memory
        print("3. ReActAgent with long term memory")
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

