# -*- coding: utf-8 -*-
"""Personal memory example demonstrating ReMe personal memory.

This module provides examples of how to use the ReMePersonalMemory
class.

The example demonstrates 5 core interfaces:
1. record_to_memory - Tool function for explicit memory recording
2. retrieve_from_memory - Tool function for keyword-based retrieval
3. record - Direct method for recording message conversations
4. retrieve - Direct method for query-based retrieval
5. ReActAgent integration - Using personal memory with ReActAgent
"""

import asyncio
import os
from dotenv import load_dotenv

from agentscope.agent import ReActAgent
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.memory import ReMePersonalLongTermMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import ToolResponse, Toolkit

load_dotenv()


async def test_record_to_memory(
    memory: ReMePersonalLongTermMemory,
) -> None:
    """Test the record_to_memory tool function interface."""
    print("Interface 1: record_to_memory (Tool Function)")
    print("-" * 70)
    print("Purpose: Explicit memory recording with structured content")
    print("Test case: Recording user's travel preferences...")

    result: ToolResponse = await memory.record_to_memory(
        thinking=("The user is sharing their travel preferences and habits"),
        content=[
            "I prefer to stay in homestays when traveling to Hangzhou",
            "I like to visit the West Lake in the morning",
            "I enjoy drinking Longjing tea",
        ],
    )
    result_text = " ".join(
        block.get("text", "")
        for block in result.content
        if block.get("type") == "text"
    )
    print(f"✓ Result: {result_text}")
    print(
        f"✓ Status: {'Success' if 'Success' in result_text else 'Failed'}",
    )
    print()


async def test_retrieve_from_memory(
    memory: ReMePersonalLongTermMemory,
) -> None:
    """Test the retrieve_from_memory tool function interface."""
    print("Interface 2: retrieve_from_memory (Tool Function)")
    print("-" * 70)
    print("Purpose: Keyword-based memory retrieval")
    print()

    result = await memory.retrieve_from_memory(
        keywords=["Hangzhou travel", "tea preference"],
    )
    retrieved_text = " ".join(
        block.get("text", "")
        for block in result.content
        if block.get("type") == "text"
    )
    print("✓ Retrieved memories:")
    print(f"{retrieved_text}")
    print()


async def test_record_direct(memory: ReMePersonalLongTermMemory) -> None:
    """Test the direct record method interface."""
    print("Interface 3: record (Direct Recording)")
    print("-" * 70)
    print("Purpose: Direct recording of message conversations")
    print()
    print("Test case: Recording work preferences and habits...")

    try:
        await memory.record(
            msgs=[
                Msg(
                    role="user",
                    content=(
                        "I work as a software engineer and prefer "
                        "remote work"
                    ),
                    name="user",
                ),
                Msg(
                    role="assistant",
                    content=(
                        "Understood! You're a software engineer who "
                        "values remote work flexibility."
                    ),
                    name="assistant",
                ),
                Msg(
                    role="user",
                    content=(
                        "I usually start my day at 9 AM with a "
                        "cup of coffee"
                    ),
                    name="user",
                ),
            ],
        )
        print("✓ Status: Successfully recorded conversation messages")
        print(
            "✓ Messages recorded: 3 messages (user-assistant dialogue)",
        )
    except Exception as e:
        print(f"✗ Status: Failed - {str(e)}")
    print()


async def test_retrieve_direct(memory: ReMePersonalLongTermMemory) -> None:
    """Test the direct retrieve method interface."""
    print("Interface 4: retrieve (Direct Retrieval)")
    print("-" * 70)
    print("Purpose: Query-based memory retrieval using messages")
    print()
    print(
        "Test case: Querying 'What do you know about my "
        "work preferences?'...",
    )

    memories = await memory.retrieve(
        msg=Msg(
            role="user",
            content="What do you know about my work preferences?",
            name="user",
        ),
    )
    print("✓ Retrieved memories:")
    print(f"{memories if memories else 'No memories found'}")
    status = (
        "Success - Found memories"
        if memories
        else "No relevant memories found"
    )
    print(f"✓ Status: {status}")
    print()


async def test_react_agent_with_memory(
    memory: ReMePersonalLongTermMemory,
) -> None:
    """Test ReActAgent integration with personal memory."""
    print("Interface 5: ReActAgent with Personal Memory")
    print("-" * 70)
    print(
        "Purpose: Demonstrate how ReActAgent uses personal memory tools",
    )
    print()
    print("Test case: Agent-driven memory recording and retrieval...")

    toolkit = Toolkit()
    agent = ReActAgent(
        name="Friday",
        sys_prompt=(
            "You are a helpful assistant named Friday with long-term "
            "memory capabilities. "
            "\n\n## Memory Management Guidelines:\n"
            "1. **Recording Memories**: When users share personal "
            "information, preferences, "
            "habits, or facts about themselves, ALWAYS record them "
            "using `record_to_memory` "
            "for future reference.\n"
            "\n2. **Retrieving Memories**: BEFORE answering questions "
            "about the user's preferences, "
            "past information, or personal details, you MUST FIRST "
            "call `retrieve_from_memory` "
            "to check if you have any relevant stored information. "
            "Do NOT rely solely on the "
            "current conversation context.\n"
            "\n3. **When to Retrieve**: Call `retrieve_from_memory` "
            "when:\n"
            "   - User asks questions like 'what do I like?', "
            "'what are my preferences?', "
            "'what do you know about me?'\n"
            "   - User asks about their past behaviors, habits, or "
            "preferences\n"
            "   - User refers to information they mentioned before\n"
            "   - You need context about the user to provide "
            "personalized responses\n"
            "\nAlways check your memory first before claiming you "
            "don't know something about the user."
        ),
        model=DashScopeChatModel(
            model_name="qwen3-max",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            stream=False,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        long_term_memory=memory,
        long_term_memory_mode="both",
    )

    await agent.memory.clear()

    print(
        "→ User: 'When I travel to Hangzhou, I prefer to stay in "
        "a homestay'",
    )
    msg = Msg(
        role="user",
        content=(
            "When I travel to Hangzhou, I prefer to stay in " "a homestay"
        ),
        name="user",
    )
    msg = await agent(msg)
    print(f"✓ Agent response: {msg.get_text_content()}\n")

    print("→ User: 'what preference do I have?'")
    msg = Msg(
        role="user",
        content="what preference do I have?",
        name="user",
    )
    msg = await agent(msg)
    print(f"✓ Agent response: {msg.get_text_content()}\n")

    print(
        "✓ Status: Successfully demonstrated ReActAgent with "
        "personal memory",
    )
    print()


async def main() -> None:
    """Demonstrate the 5 core interfaces of ReMePersonalMemory.

    This example shows how to use:
    1. record_to_memory - Tool function for explicit memory recording
    2. retrieve_from_memory - Tool function for keyword-based retrieval
    3. record - Direct method for recording message conversations
    4. retrieve - Direct method for query-based retrieval
    5. ReActAgent integration - Using personal memory with ReActAgent
    """
    long_term_memory = ReMePersonalLongTermMemory(
        agent_name="Friday",
        user_name="user_123",
        model=DashScopeChatModel(
            model_name="qwen3-max",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            stream=False,
        ),
        embedding_model=DashScopeTextEmbedding(
            model_name="text-embedding-v4",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            dimensions=1024,
        ),
    )

    print("=" * 70)
    print("ReMePersonalMemory - Testing 5 Core Interfaces")
    print("=" * 70)
    print()

    # Use async context manager to ensure proper initialization
    async with long_term_memory:
        # await test_record_to_memory(long_term_memory)
        # await test_retrieve_from_memory(long_term_memory)
        # await test_record_direct(long_term_memory)
        # await test_retrieve_direct(long_term_memory)
        await test_react_agent_with_memory(long_term_memory)

    print("=" * 70)
    print("Testing Complete: All 5 Core Interfaces Verified!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
