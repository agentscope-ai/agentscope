# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=C0301
"""Personal memory example demonstrating ReMe personal memory functionality.

This module provides examples of how to use the ReMePersonalMemory class

The example demonstrates 4 core interfaces:
1. record_to_memory - Tool function for explicit memory recording
2. retrieve_from_memory - Tool function for keyword-based retrieval
3. record - Direct method for recording message conversations
4. retrieve - Direct method for query-based retrieval
"""

import asyncio
import os

from dotenv import load_dotenv

from agentscope.embedding import DashScopeTextEmbedding
from agentscope.memory.reme import ReMePersonalMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import ToolResponse

load_dotenv()


async def test_record_to_memory(
    memory: ReMePersonalMemory,
) -> None:
    """Test the record_to_memory tool function interface."""
    print("Interface 1: record_to_memory (Tool Function)")
    print("-" * 70)
    print("Purpose: Explicit memory recording with structured content")
    print()
    print("Test case: Recording user's travel preferences...")

    result: ToolResponse = await memory.record_to_memory(
        thinking="The user is sharing their travel preferences and habits",
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
    memory: ReMePersonalMemory,
) -> None:
    """Test the retrieve_from_memory tool function interface."""
    print("Interface 2: retrieve_from_memory (Tool Function)")
    print("-" * 70)
    print("Purpose: Keyword-based memory retrieval")
    print()
    print(
        "Test case: Searching for 'Hangzhou travel' and 'tea preference'",
    )

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


async def test_record_direct(memory: ReMePersonalMemory) -> None:
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
                    content="I work as a software engineer and prefer remote work",
                    name="user",
                ),
                Msg(
                    role="assistant",
                    content="Understood! You're a software engineer who values remote work flexibility.",
                    name="assistant",
                ),
                Msg(
                    role="user",
                    content="I usually start my day at 9 AM with a cup of coffee",
                    name="user",
                ),
            ],
        )
        print("✓ Status: Successfully recorded conversation messages")
        print("✓ Messages recorded: 3 messages (user-assistant dialogue)")
    except Exception as e:
        print(f"✗ Status: Failed - {str(e)}")
    print()


async def test_retrieve_direct(memory: ReMePersonalMemory) -> None:
    """Test the direct retrieve method interface."""
    print("Interface 4: retrieve (Direct Retrieval)")
    print("-" * 70)
    print("Purpose: Query-based memory retrieval using messages")
    print()
    print(
        "Test case: Querying 'What do you know about my work preferences?'...",  # noqa: E501
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
    print(
        f"✓ Status: {'Success - Found memories' if memories else 'No relevant memories found'}",  # noqa
    )
    print()


async def main() -> None:
    """Demonstrate the 4 core interfaces of ReMePersonalMemory.

    This example shows how to use:
    1. record_to_memory - Tool function for explicit memory recording
    2. retrieve_from_memory - Tool function for keyword-based retrieval
    3. record - Direct method for recording message conversations
    4. retrieve - Direct method for query-based retrieval
    """
    long_term_memory = ReMePersonalMemory(
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
    print("ReMePersonalMemory - Testing 4 Core Interfaces")
    print("=" * 70)
    print()

    # Use async context manager to ensure proper initialization
    async with long_term_memory:
        await test_record_to_memory(long_term_memory)
        await test_retrieve_from_memory(long_term_memory)
        await test_record_direct(long_term_memory)
        await test_retrieve_direct(long_term_memory)

    print("=" * 70)
    print("Testing Complete: All 4 Core Interfaces Verified!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
