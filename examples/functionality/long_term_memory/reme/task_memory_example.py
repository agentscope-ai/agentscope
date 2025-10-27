# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=C0301
"""Task memory example demonstrating ReMe task memory functionality.

This module provides examples of how to use the ReMeTaskMemory class
using the ReMe library.

The example demonstrates 4 core interfaces:
1. record_to_memory - Tool function for recording task information
2. retrieve_from_memory - Tool function for keyword-based retrieval
3. record - Direct method for recording message conversations with scores
4. retrieve - Direct method for retrieving task experiences
"""

import asyncio
import os

from dotenv import load_dotenv

from agentscope.embedding import DashScopeTextEmbedding
from agentscope.memory.reme import ReMeTaskMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import ToolResponse

load_dotenv()


async def test_record_to_memory(memory: ReMeTaskMemory) -> None:
    """Test the record_to_memory tool function interface."""
    print("Interface 1: record_to_memory (Tool Function)")
    print("-" * 70)
    print(
        "Purpose: Record task execution information with thinking and content",
    )
    print()
    print("Test case: Recording project planning task information...")

    result: ToolResponse = await memory.record_to_memory(
        thinking="Recording project planning best practices and development approach",
        content=[
            "For web application projects, break down into phases: Requirements gathering, Design, Development, Testing, Deployment",
            "Development phase recommendations: Frontend (React), Backend (FastAPI), Database (PostgreSQL), Agile methodology with 2-week sprints",
            "Dependency management: Use npm for frontend and pip for Python backend, maintain requirements.txt and package.json files",
        ],
        score=0.9,  # Optional: score for this trajectory (default is 1.0)
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


async def test_retrieve_from_memory(memory: ReMeTaskMemory) -> None:
    """Test the retrieve_from_memory tool function interface."""
    print("Interface 2: retrieve_from_memory (Tool Function)")
    print("-" * 70)
    print("Purpose: Keyword-based retrieval of task experiences")
    print()
    print(
        "Test case: Searching with keywords 'project planning', 'development phase'...",
    )

    result = await memory.retrieve_from_memory(
        keywords=["project planning", "development phase"],
        # top_k defaults to 5
    )
    retrieved_text = " ".join(
        block.get("text", "")
        for block in result.content
        if block.get("type") == "text"
    )
    print("✓ Retrieved experiences:")
    print(f"{retrieved_text}")
    print(
        f"✓ Status: {'Success - Found experiences' if retrieved_text and 'No task experiences found' not in retrieved_text else 'No relevant experiences found'}",
    )
    print()


async def test_record_direct(memory: ReMeTaskMemory) -> None:
    """Test the direct record method interface."""
    print("Interface 3: record (Direct Recording)")
    print("-" * 70)
    print("Purpose: Direct recording of message conversations with scores")
    print()
    print("Test case: Recording debugging task conversation...")

    try:
        await memory.record(
            msgs=[
                Msg(
                    role="user",
                    content="I'm getting a 404 error on my API endpoint",
                    name="user",
                ),
                Msg(
                    role="assistant",
                    content="Let's troubleshoot: 1) Check if the route is properly defined, 2) Verify the URL path, 3) Ensure the server is running on the correct port",
                    name="assistant",
                ),
                Msg(
                    role="user",
                    content="Found it! The route path had a typo.",
                    name="user",
                ),
                Msg(
                    role="assistant",
                    content="Great! Always double-check route paths and use a linter to catch typos early.",
                    name="assistant",
                ),
            ],
            score=0.95,  # Optional: score for successful resolution (default: 1.0)
        )
        print("✓ Status: Successfully recorded debugging trajectory")
        print("✓ Messages recorded: 4 messages with score 0.95")
    except Exception as e:
        print(f"✗ Status: Failed - {str(e)}")
    print()


async def test_retrieve_direct(memory: ReMeTaskMemory) -> None:
    """Test the direct retrieve method interface."""
    print("Interface 4: retrieve (Direct Retrieval)")
    print("-" * 70)
    print("Purpose: Query-based retrieval using messages")
    print()
    print("Test case: Querying 'How to debug API errors?'...")

    memories = await memory.retrieve(
        msg=Msg(
            role="user",
            content="How should I approach debugging API errors in my application?",
            name="user",
        ),
        # top_k defaults to 5
    )
    print("✓ Retrieved experiences:")
    print(f"{memories if memories else 'No experiences found'}")
    print(
        f"✓ Status: {'Success - Found experiences' if memories else 'No relevant experiences found'}",
    )
    print()


async def main() -> None:
    """Demonstrate the 4 core interfaces of ReMeTaskMemory.

    This example shows how to use:
    1. record_to_memory - Tool function for recording task information
    2. retrieve_from_memory - Tool function for keyword-based retrieval
    3. record - Direct method for recording message conversations with scores
    4. retrieve - Direct method for retrieving task experiences
    """
    long_term_memory = ReMeTaskMemory(
        agent_name="TaskAssistant",
        user_name="task_workspace_123",
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
    print("ReMeTaskMemory - Testing 4 Core Interfaces")
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
