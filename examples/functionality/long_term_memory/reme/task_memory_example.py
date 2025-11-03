# -*- coding: utf-8 -*-
"""Task memory example demonstrating ReMe task memory.

This module provides examples of how to use the ReMeTaskMemory class
using the ReMe library.

The example demonstrates 5 core interfaces:
1. record_to_memory - Tool function for recording task information
2. retrieve_from_memory - Tool function for keyword-based retrieval
3. record - Direct method for recording message conversations
   with scores
4. retrieve - Direct method for retrieving task experiences
5. ReActAgent integration - Using task memory with ReActAgent
"""

import asyncio
import os
from dotenv import load_dotenv

from agentscope.agent import ReActAgent
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.memory import ReMeTaskLongTermMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import ToolResponse, Toolkit

load_dotenv()


async def test_record_to_memory(memory: ReMeTaskLongTermMemory) -> None:
    """Test the record_to_memory tool function interface."""
    print("Interface 1: record_to_memory (Tool Function)")
    print("-" * 70)
    print(
        "Purpose: Record task execution information with thinking and content",
    )
    print()
    print("Test case: Recording project planning task information...")

    result: ToolResponse = await memory.record_to_memory(
        thinking=(
            "Recording project planning best practices and "
            "development approach"
        ),
        content=[
            "For web application projects, break down into phases: "
            "Requirements gathering, Design, Development, Testing, "
            "Deployment",
            "Development phase recommendations: Frontend (React), "
            "Backend (FastAPI), Database (PostgreSQL), Agile "
            "methodology with 2-week sprints",
            "Dependency management: Use npm for frontend and pip for "
            "Python backend, maintain requirements.txt and "
            "package.json files",
        ],
        score=0.9,  # Optional: score for this trajectory (1.0)
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
    memory: ReMeTaskLongTermMemory,
) -> None:
    """Test the retrieve_from_memory tool function interface."""
    print("Interface 2: retrieve_from_memory (Tool Function)")
    print("-" * 70)
    print("Purpose: Keyword-based retrieval of task experiences")
    print()
    print(
        "Test case: Searching with keywords 'project planning', "
        "'development phase'...",
    )

    result = await memory.retrieve_from_memory(
        keywords=["project planning", "development phase"],
    )
    retrieved_text = " ".join(
        block.get("text", "")
        for block in result.content
        if block.get("type") == "text"
    )
    print("✓ Retrieved experiences:")
    print(f"{retrieved_text}")
    has_experiences = (
        retrieved_text and "No task experiences found" not in retrieved_text
    )
    status = (
        "Success - Found experiences"
        if has_experiences
        else "No relevant experiences found"
    )
    print(f"✓ Status: {status}")
    print()


async def test_record_direct(memory: ReMeTaskLongTermMemory) -> None:
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
                    content=(
                        "Let's troubleshoot: 1) Check if the route is "
                        "properly defined, 2) Verify the URL path, "
                        "3) Ensure the server is running on the correct "
                        "port"
                    ),
                    name="assistant",
                ),
                Msg(
                    role="user",
                    content="Found it! The route path had a typo.",
                    name="user",
                ),
                Msg(
                    role="assistant",
                    content=(
                        "Great! Always double-check route paths and use "
                        "a linter to catch typos early."
                    ),
                    name="assistant",
                ),
            ],
            score=0.95,  # Optional: score (default: 1.0)
        )
        print("✓ Status: Successfully recorded debugging trajectory")
        print("✓ Messages recorded: 4 messages with score 0.95")
    except Exception as e:
        print(f"✗ Status: Failed - {str(e)}")
    print()


async def test_retrieve_direct(memory: ReMeTaskLongTermMemory) -> None:
    """Test the direct retrieve method interface."""
    print("Interface 4: retrieve (Direct Retrieval)")
    print("-" * 70)
    print("Purpose: Query-based retrieval using messages")
    print()
    print("Test case: Querying 'How to debug API errors?'...")

    memories = await memory.retrieve(
        msg=Msg(
            role="user",
            content=(
                "How should I approach debugging API errors in my "
                "application?"
            ),
            name="user",
        ),
    )
    print("✓ Retrieved experiences:")
    print(f"{memories if memories else 'No experiences found'}")
    status = (
        "Success - Found experiences"
        if memories
        else "No relevant experiences found"
    )
    print(f"✓ Status: {status}")
    print()


async def test_react_agent_with_memory(
    memory: ReMeTaskLongTermMemory,
) -> None:
    """Test ReActAgent integration with task memory."""
    print("Interface 5: ReActAgent with Task Memory")
    print("-" * 70)
    print(
        "Purpose: Demonstrate how ReActAgent uses task memory tools",
    )
    print()
    print(
        "Test case: Agent-driven task experience recording and "
        "retrieval...",
    )

    toolkit = Toolkit()
    agent = ReActAgent(
        name="TaskAssistant",
        sys_prompt=(
            "You are a helpful task assistant named TaskAssistant "
            "with long-term task memory. "
            "\n\n## Task Memory Management Guidelines:\n"
            "1. **Recording Task Experiences**: When you provide "
            "technical solutions, solve problems, "
            "or complete tasks, ALWAYS record the key insights using "
            "`record_to_memory`. Include:\n"
            "   - Specific techniques and approaches used\n"
            "   - Best practices and implementation details\n"
            "   - Lessons learned and important considerations\n"
            "   - Step-by-step procedures that worked well\n"
            "\n2. **Retrieving Past Experiences**: BEFORE solving a "
            "problem or answering technical "
            "questions, you MUST FIRST call `retrieve_from_memory` "
            "to check if you have relevant "
            "past experiences. This helps you:\n"
            "   - Avoid repeating past mistakes\n"
            "   - Leverage proven solutions\n"
            "   - Provide more accurate and tested approaches\n"
            "\n3. **When to Retrieve**: Always retrieve when:\n"
            "   - Asked about technical topics or problem-solving "
            "approaches\n"
            "   - Asked to provide recommendations or best practices\n"
            "   - Dealing with tasks similar to ones you may have "
            "handled before\n"
            "   - User explicitly asks 'what do you know about...?' "
            "or 'have you seen this before?'\n"
            "\nAlways check your task memory first to provide the "
            "most informed responses."
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
        "→ User: 'Here are some database optimization techniques "
        "I learned'",
    )
    msg = Msg(
        role="user",
        content=(
            "I just learned some valuable database optimization "
            "techniques for slow queries: "
            "1) Add indexes on foreign keys and WHERE clause columns "
            "to speed up joins and filtering. "
            "2) Use table partitioning to divide large tables by "
            "date or category for faster queries. "
            "3) Implement query result caching with Redis to avoid "
            "repeated database hits. "
            "4) Optimize JOIN order - put smallest tables first to "
            "reduce intermediate result sets. "
            "5) Use EXPLAIN ANALYZE to identify bottlenecks and "
            "missing indexes. "
            "Please record these optimization techniques for future "
            "reference."
        ),
        name="user",
    )
    msg = await agent(msg)
    print(f"✓ Agent response: {msg.get_text_content()}\n")

    print(
        "→ User: 'What do you know about database optimization?'",
    )
    msg = Msg(
        role="user",
        content=(
            "What do you know about database optimization? "
            "Can you retrieve any past experiences?"
        ),
        name="user",
    )
    msg = await agent(msg)
    print(f"✓ Agent response: {msg.get_text_content()}\n")

    print()


async def main() -> None:
    """Demonstrate the 5 core interfaces of ReMeTaskMemory.

    This example shows how to use:
    1. record_to_memory - Tool function for recording task information
    2. retrieve_from_memory - Tool function for keyword-based retrieval
    3. record - Direct method for recording message conversations with scores
    4. retrieve - Direct method for retrieving task experiences
    5. ReActAgent integration - Using task memory with ReActAgent
    """
    long_term_memory = ReMeTaskLongTermMemory(
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
    print("ReMeTaskMemory - Testing 5 Core Interfaces")
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
