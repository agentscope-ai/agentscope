# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=C0301
"""Tool memory example demonstrating ReMe tool memory functionality.

This module provides examples of how to use the ReMeToolMemory class
for recording tool execution results and retrieving tool usage guidelines
using the ReMe library.

The example demonstrates 4 core interfaces:
1. record_to_memory - Tool function for recording tool execution results (JSON format)
2. retrieve_from_memory - Tool function for retrieving tool usage guidelines
3. record - Direct method for recording tool-related messages
4. retrieve - Direct method for retrieving tool guidelines from messages
"""

import asyncio
import json
import os
from datetime import datetime

from dotenv import load_dotenv

from agentscope.embedding import DashScopeTextEmbedding
from agentscope.memory.reme import ReMeToolMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel

load_dotenv()


async def test_record_to_memory(memory: ReMeToolMemory) -> None:
    """Test the record_to_memory tool function interface."""
    print("Interface 1: record_to_memory (Tool Function)")
    print("-" * 70)
    print(
        "Purpose: Record tool execution results with detailed metadata in JSON format",
    )
    print()
    print(
        "Test case: Recording web_search and file_read tool executions...",
    )

    # Prepare tool call results as JSON strings
    tool_results = [
        {
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": "web_search",
            "input": {
                "query": "Python asyncio best practices",
                "max_results": 10,
            },
            "output": "Found 10 relevant articles about asyncio patterns, including event loops, coroutines, and concurrent execution strategies.",
            "token_cost": 150,
            "success": True,
            "time_cost": 2.3,
        },
        {
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": "file_read",
            "input": {
                "file_path": "/path/to/config.yaml",
                "encoding": "utf-8",
            },
            "output": "Successfully read 1024 bytes from config.yaml",
            "token_cost": 50,
            "success": True,
            "time_cost": 0.1,
        },
        {
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": "web_search",
            "input": {
                "query": "invalid@#$query",
                "max_results": 5,
            },
            "output": "Error: Invalid query format",
            "token_cost": 20,
            "success": False,
            "time_cost": 0.5,
        },
    ]

    # Convert to JSON strings as required by the interface
    content = [json.dumps(result) for result in tool_results]

    result = await memory.record_to_memory(
        thinking="Recording tool execution results for web_search and file_read to learn usage patterns",
        content=content,
    )

    result_text = " ".join(
        block.get("text", "")
        for block in result.content
        if block.get("type") == "text"
    )
    print(f"✓ Result: {result_text}")
    print(
        f"✓ Status: {'Success' if 'Successfully recorded' in result_text else 'Failed'}",
    )
    print()


async def test_retrieve_from_memory(memory: ReMeToolMemory) -> None:
    """Test the retrieve_from_memory tool function interface."""
    print("Interface 2: retrieve_from_memory (Tool Function)")
    print("-" * 70)
    print("Purpose: Retrieve tool usage guidelines using tool names")
    print()
    print("Test case: Retrieving guidelines for web_search tool...")

    result = await memory.retrieve_from_memory(
        keywords=["web_search"],
    )

    retrieved_text = " ".join(
        block.get("text", "")
        for block in result.content
        if block.get("type") == "text"
    )
    print("✓ Retrieved guidelines:")
    print(f"{retrieved_text}")
    print(
        f"✓ Status: {'Success' if retrieved_text and 'Error' not in retrieved_text else 'Failed'}",
    )
    print()

    # Additional example: Retrieve multiple tools
    print("Test case: Retrieving guidelines for multiple tools...")
    result = await memory.retrieve_from_memory(
        keywords=["web_search", "file_read"],
    )
    retrieved_text = " ".join(
        block.get("text", "")
        for block in result.content
        if block.get("type") == "text"
    )
    print("✓ Retrieved guidelines for multiple tools:")
    print(f"{retrieved_text}")
    print()


async def test_record_direct(memory: ReMeToolMemory) -> None:
    """Test the direct record method interface."""
    print("Interface 3: record (Direct Method)")
    print("-" * 70)
    print(
        "Purpose: Direct method for recording tool execution results from messages",
    )
    print()
    print("Test case: Recording tool results via Msg objects...")

    # Prepare more tool results as JSON strings
    api_result = {
        "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tool_name": "api_call",
        "input": {
            "endpoint": "https://api.example.com/data",
            "method": "GET",
            "headers": {"Authorization": "Bearer token"},
        },
        "output": '{"status": "success", "data": [...]}',
        "token_cost": 200,
        "success": True,
        "time_cost": 1.5,
    }

    data_result = {
        "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tool_name": "data_process",
        "input": {
            "data": "[1, 2, 3, 4, 5]",
            "operation": "average",
        },
        "output": "Average: 3.0",
        "token_cost": 30,
        "success": True,
        "time_cost": 0.05,
    }

    try:
        await memory.record(
            msgs=[
                Msg(
                    role="assistant",
                    content=json.dumps(api_result),
                    name="assistant",
                ),
                Msg(
                    role="assistant",
                    content=json.dumps(data_result),
                    name="assistant",
                ),
            ],
        )
        print(
            "✓ Status: Successfully recorded tool results via direct method",
        )
        print(
            "✓ Note: Messages with JSON-formatted tool results were processed and summarized",
        )
    except Exception as e:
        print(f"✗ Status: Failed - {str(e)}")
    print()


async def test_retrieve_direct(memory: ReMeToolMemory) -> None:
    """Test the direct retrieve method interface."""
    print("Interface 4: retrieve (Direct Method)")
    print("-" * 70)
    print(
        "Purpose: Direct method for retrieving tool guidelines from message",
    )
    print()
    print("Test case: Querying for 'api_call' tool information...")

    memories = await memory.retrieve(
        msg=Msg(
            role="user",
            content="api_call",
            name="user",
        ),
    )
    print("✓ Retrieved guidelines:")
    print(f"{memories if memories else 'No guidelines found'}")
    print(
        f"✓ Status: {'Success - Found guidelines' if memories else 'No relevant guidelines found'}",
    )
    print()


async def main() -> None:
    """Demonstrate the 4 core interfaces of ReMeToolMemory.

    This example shows how to use:
    1. record_to_memory - Tool function for recording tool execution results
    2. retrieve_from_memory - Tool function for retrieving tool usage guidelines
    3. record - Direct method for recording tool-related messages
    4. retrieve - Direct method for retrieving tool guidelines from messages
    """
    long_term_memory = ReMeToolMemory(
        agent_name="ToolAssistant",
        user_name="tool_workspace_123",  # This serves as workspace_id in ReMe
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
    print("ReMeToolMemory - Testing 4 Core Interfaces")
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
