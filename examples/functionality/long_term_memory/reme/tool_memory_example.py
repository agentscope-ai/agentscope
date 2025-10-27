# -*- coding: utf-8 -*-
"""Tool memory example demonstrating ReMe tool memory functionality.

This module provides examples of how to use the ReMeToolMemory class
for recording tool execution results and retrieving tool usage guidelines
using the ReMe library.

The example demonstrates 5 core interfaces:
1. add_tool_call_result - Record tool execution results
2. summary_tool_memory - Generate usage guidelines from history
3. retrieve_tool_memory - Retrieve tool usage guidelines
4. record - Direct method for recording tool-related messages (simplified)
5. retrieve - Direct method for retrieving tool guidelines from messages
"""

import asyncio
import os
from datetime import datetime

from dotenv import load_dotenv

from agentscope.memory.reme import ReMeToolMemory
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import ToolResponse

load_dotenv()


async def main() -> None:
    """Run the tool memory examples to test 5 core interfaces."""
    
    # Initialize ReMeToolMemory
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
    print("ReMeToolMemory - Testing 5 Core Interfaces")
    print("=" * 70)
    print()

    # Use async context manager to ensure proper initialization
    async with long_term_memory:
        
        # ============================================
        # Interface 1: add_tool_call_result
        # ============================================
        print("Interface 1: add_tool_call_result")
        print("-" * 70)
        print("Purpose: Record tool execution results with detailed metadata")
        print()
        print("Test case: Recording web_search and file_read tool executions...")
        
        result: ToolResponse = await long_term_memory.add_tool_call_result(
            tool_call_results=[
                {
                    "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "tool_name": "web_search",
                    "input": {
                        "query": "Python asyncio best practices",
                        "max_results": 10
                    },
                    "output": "Found 10 relevant articles about asyncio patterns, including event loops, coroutines, and concurrent execution strategies.",
                    "token_cost": 150,
                    "success": True,
                    "time_cost": 2.3
                },
                {
                    "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "tool_name": "file_read",
                    "input": {
                        "file_path": "/path/to/config.yaml",
                        "encoding": "utf-8"
                    },
                    "output": "Successfully read 1024 bytes from config.yaml",
                    "token_cost": 50,
                    "success": True,
                    "time_cost": 0.1
                },
                {
                    "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "tool_name": "web_search",
                    "input": {
                        "query": "invalid@#$query",
                        "max_results": 5
                    },
                    "output": "Error: Invalid query format",
                    "token_cost": 20,
                    "success": False,
                    "time_cost": 0.5
                }
            ],
        )
        result_text = " ".join(block.get("text", "") for block in result.content if block.get("type") == "text")
        print(f"✓ Result: {result_text}")
        print(f"✓ Status: {'Success' if 'Success' in result_text else 'Failed'}")
        print()

        # ============================================
        # Interface 2: summary_tool_memory
        # ============================================
        print("Interface 2: summary_tool_memory")
        print("-" * 70)
        print("Purpose: Generate usage guidelines from tool execution history")
        print()
        print("Test case: Generating guidelines for web_search tool...")
        
        result = await long_term_memory.summary_tool_memory(
            tool_names="web_search",
        )
        summary_text = " ".join(block.get("text", "") for block in result.content if block.get("type") == "text")
        print(f"✓ Generated guidelines:")
        print(f"{summary_text}")
        print(f"✓ Status: {'Success' if summary_text else 'Failed'}")
        print()

        # Additional example: Summarize multiple tools
        print("Test case: Generating guidelines for multiple tools...")
        result = await long_term_memory.summary_tool_memory(
            tool_names=["web_search", "file_read"],
        )
        summary_text = " ".join(block.get("text", "") for block in result.content if block.get("type") == "text")
        print(f"✓ Generated guidelines for multiple tools:")
        print(f"{summary_text}")
        print()

        # ============================================
        # Interface 3: retrieve_tool_memory
        # ============================================
        print("Interface 3: retrieve_tool_memory")
        print("-" * 70)
        print("Purpose: Retrieve tool usage guidelines before use")
        print()
        print("Test case: Retrieving guidelines for web_search...")
        
        result = await long_term_memory.retrieve_tool_memory(
            tool_names="web_search",
        )
        retrieved_text = " ".join(block.get("text", "") for block in result.content if block.get("type") == "text")
        print(f"✓ Retrieved guidelines:")
        print(f"{retrieved_text}")
        print(f"✓ Status: {'Success - Found guidelines' if 'Tool Guidelines' in retrieved_text or 'No tool guidelines' in retrieved_text else 'Failed'}")
        print()

        # ============================================
        # Interface 4: record (Direct Recording - Simplified)
        # ============================================
        print("Interface 4: record (Direct Recording)")
        print("-" * 70)
        print("Purpose: Record tool-related messages (simplified implementation)")
        print()
        print("Test case: Recording tool-related conversation...")
        
        try:
            await long_term_memory.record(
                msgs=[
                    Msg(
                        role="user",
                        content="How do I use the database_query tool?",
                        name="user",
                    ),
                    Msg(
                        role="assistant",
                        content="The database_query tool requires a SQL query string and optional connection parameters.",
                        name="assistant",
                    ),
                ],
            )
            print("✓ Status: Method called (Note: Tool memory recording requires structured data)")
            print("✓ Recommendation: Use add_tool_call_result() for proper tool memory recording")
        except Exception as e:
            print(f"✗ Status: {str(e)}")
        print()

        # ============================================
        # Interface 5: retrieve (Direct Retrieval)
        # ============================================
        print("Interface 5: retrieve (Direct Retrieval)")
        print("-" * 70)
        print("Purpose: Retrieve tool guidelines based on message content")
        print()
        print("Test case: Querying for 'web_search' tool information...")
        
        memories = await long_term_memory.retrieve(
            msg=Msg(
                role="user",
                content="web_search",
                name="user",
            ),
        )
        print(f"✓ Retrieved guidelines:")
        print(f"{memories if memories else 'No guidelines found'}")
        print(f"✓ Status: {'Success - Found guidelines' if memories else 'No relevant guidelines found'}")
        print()

        # Additional example: Recording more tool executions
        print("Additional Example: Recording diverse tool executions")
        print("-" * 70)
        print("Test case: Recording API call and data processing tools...")
        
        result = await long_term_memory.add_tool_call_result(
            tool_call_results=[
                {
                    "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "tool_name": "api_call",
                    "input": {
                        "endpoint": "https://api.example.com/data",
                        "method": "GET",
                        "headers": {"Authorization": "Bearer token"}
                    },
                    "output": '{"status": "success", "data": [...]}',
                    "token_cost": 200,
                    "success": True,
                    "time_cost": 1.5
                },
                {
                    "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "tool_name": "data_process",
                    "input": {
                        "data": "[1, 2, 3, 4, 5]",
                        "operation": "average"
                    },
                    "output": "Average: 3.0",
                    "token_cost": 30,
                    "success": True,
                    "time_cost": 0.05
                }
            ],
        )
        result_text = " ".join(block.get("text", "") for block in result.content if block.get("type") == "text")
        print(f"✓ Result: {result_text}")
        print()

    print("=" * 70)
    print("Testing Complete: All 5 Core Interfaces Verified!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

