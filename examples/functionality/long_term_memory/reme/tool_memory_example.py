# -*- coding: utf-8 -*-
"""Tool memory example demonstrating ReMe tool memory with ReActAgent.

This module demonstrates the complete workflow:
1. Mock a tool function and register it to Toolkit
2. Record tool execution results to tool memory using record()
3. Retrieve tool usage guidelines using retrieve()
4. Inject guidelines into ReActAgent's system prompt
5. Use ReActAgent with tool memory

This workflow helps LLMs learn from past tool usage patterns and
improve their tool calling decisions over time.
"""

import asyncio
import json
import os
from datetime import datetime
from dotenv import load_dotenv

from agentscope.agent import ReActAgent
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.memory import ReMeToolLongTermMemory
from agentscope.message import Msg
from agentscope.message import TextBlock
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit, ToolResponse

load_dotenv()


# ============================================================================
# Step 1: Mock tool functions
# ============================================================================


async def web_search(query: str, max_results: int = 5) -> ToolResponse:
    """Search the web for information.

    Args:
        query: The search query string
        max_results: Maximum number of results to return

    Returns:
        ToolResponse containing search results
    """
    # Simulate web search
    result = f"Found {max_results} results for query: '{query}'"
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=result,
            ),
        ],
    )


async def calculate(expression: str) -> ToolResponse:
    """Calculate a mathematical expression.

    Args:
        expression: Mathematical expression to evaluate

    Returns:
        ToolResponse containing calculation result
    """
    try:
        # Simple calculation (in real scenario, use safer evaluation)
        result = eval(expression)  # noqa: S307
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Result: {result}",
                ),
            ],
        )
    except Exception as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error calculating '{expression}': {str(e)}",
                ),
            ],
        )


# ============================================================================
# Step 2: Record tool execution history to tool memory
# ============================================================================


async def record_tool_history(
    tool_memory: ReMeToolLongTermMemory,
) -> None:
    """Record historical tool execution results to tool memory.

    This simulates past tool usage that the agent can learn from.
    """
    print("=" * 70)
    print("Step 1: Recording Tool Execution History to Memory")
    print("=" * 70)
    print()

    # Record successful web_search examples
    web_search_histories = [
        {
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": "web_search",
            "input": {
                "query": "Python asyncio tutorial",
                "max_results": 10,
            },
            "output": (
                "Found 10 results for query: 'Python asyncio tutorial'"
            ),
            "token_cost": 150,
            "success": True,
            "time_cost": 2.3,
        },
        {
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": "web_search",
            "input": {
                "query": "machine learning basics",
                "max_results": 5,
            },
            "output": ("Found 5 results for query: 'machine learning basics'"),
            "token_cost": 120,
            "success": True,
            "time_cost": 1.8,
        },
    ]

    # Record failed web_search example (empty query)
    web_search_fail = {
        "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tool_name": "web_search",
        "input": {
            "query": "",
            "max_results": 5,
        },
        "output": "Error: Query cannot be empty",
        "token_cost": 20,
        "success": False,
        "time_cost": 0.1,
    }

    # Record calculate examples
    calculate_histories = [
        {
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": "calculate",
            "input": {
                "expression": "2 + 2",
            },
            "output": "Result: 4",
            "token_cost": 30,
            "success": True,
            "time_cost": 0.05,
        },
        {
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": "calculate",
            "input": {
                "expression": "10 * 5 + 3",
            },
            "output": "Result: 53",
            "token_cost": 30,
            "success": True,
            "time_cost": 0.05,
        },
    ]

    # Record all histories
    all_histories = (
        web_search_histories + [web_search_fail] + calculate_histories
    )

    print(f"Recording {len(all_histories)} tool execution histories...")
    await tool_memory.record(
        msgs=[
            Msg(
                role="assistant",
                content=json.dumps(history),
                name="assistant",
            )
            for history in all_histories
        ],
    )
    print(f"✓ Successfully recorded {len(all_histories)} tool executions")
    print()


# ============================================================================
# Step 3: Retrieve tool guidelines and create enhanced system prompt
# ============================================================================


async def retrieve_tool_guidelines(
    tool_memory: ReMeToolLongTermMemory,
    tool_names: list[str],
) -> str:
    """Retrieve tool usage guidelines from memory.

    Args:
        tool_memory: The ReMeToolMemory instance
        tool_names: List of tool names to retrieve guidelines for

    Returns:
        Combined guidelines text to be added to system prompt
    """
    print("=" * 70)
    print("Step 2: Retrieving Tool Usage Guidelines from Memory")
    print("=" * 70)
    print()

    all_guidelines = []

    for tool_name in tool_names:
        print(f"Retrieving guidelines for '{tool_name}'...")
        guidelines = await tool_memory.retrieve(
            msg=Msg(
                role="user",
                content=tool_name,
                name="user",
            ),
        )

        if guidelines:
            all_guidelines.append(
                f"## Guidelines for {tool_name}:\n{guidelines}",
            )
            print(f"✓ Retrieved guidelines for '{tool_name}'")
            print(f"  Preview: {guidelines}")
        else:
            print(
                f"✓ No guidelines found for '{tool_name}' " "(first time use)",
            )
        print()

    if all_guidelines:
        combined_guidelines = "\n\n".join(all_guidelines)
        guidelines_prompt = f"""
# Tool Usage Guidelines (from past experience)

{combined_guidelines}

Please follow these guidelines when using the tools.
"""
        return guidelines_prompt
    else:
        return ""


# ============================================================================
# Step 4: Use ReActAgent with tool memory
# ============================================================================


async def use_react_agent_with_tool_memory(
    toolkit: Toolkit,
    tool_guidelines: str,
) -> None:
    """Create and use ReActAgent with tool memory guidelines.

    Args:
        toolkit: The Toolkit with registered tools
        tool_guidelines: Retrieved tool usage guidelines
    """
    print("=" * 70)
    print("Step 3: Using ReActAgent with Tool Memory")
    print("=" * 70)
    print()

    # Create enhanced system prompt with tool guidelines
    base_sys_prompt = (
        "You are a helpful AI assistant named ToolBot.\n"
        "You have access to various tools to help users complete "
        "their tasks.\n"
        "Please use the tools appropriately based on the user's "
        "requests."
    )

    if tool_guidelines:
        sys_prompt = f"{base_sys_prompt}\n{tool_guidelines}"
        print(
            "✓ System prompt enhanced with tool memory guidelines",
        )
    else:
        sys_prompt = base_sys_prompt
        print(
            "✓ Using base system prompt (no guidelines available)",
        )

    print()

    # Create ReActAgent
    agent = ReActAgent(
        name="ToolBot",
        sys_prompt=sys_prompt,
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            stream=False,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=5,
    )

    print("✓ ReActAgent created successfully")
    print()

    # Test queries
    test_queries = [
        "Search the web for 'Python design patterns'",
        "Calculate 15 * 7 + 23",
    ]

    print("-" * 70)
    print("Testing ReActAgent with tool memory...")
    print("-" * 70)
    print()

    for i, query in enumerate(test_queries, 1):
        print(f"Query {i}: {query}")
        print("-" * 70)

        msg = Msg(
            role="user",
            content=query,
            name="user",
        )

        response = await agent(msg)
        print(f"Response: {response.get_text_content()}")
        print()
        print()


async def main() -> None:
    """Demonstrate the workflow of using ReMeToolMemory with ReActAgent.

    This example shows:
    1. Create mock tools and register them to Toolkit
    2. Record historical tool execution results to tool memory
    3. Retrieve tool usage guidelines from memory
    4. Inject guidelines into ReActAgent's system prompt
    5. Use ReActAgent to complete tasks with tool memory
    """
    print("=" * 70)
    print("ReMeToolMemory + ReActAgent Integration Example")
    print("=" * 70)
    print()
    print("This workflow demonstrates:")
    print("1. Mock tools → Register to Toolkit")
    print("2. Record tool execution history → Tool Memory")
    print("3. Retrieve guidelines → Enhance system prompt")
    print("4. Use ReActAgent with tool memory")
    print()

    # Initialize tool memory
    tool_memory = ReMeToolLongTermMemory(
        agent_name="ToolBot",
        user_name="tool_workspace_demo",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            stream=False,
        ),
        embedding_model=DashScopeTextEmbedding(
            model_name="text-embedding-v4",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            dimensions=1024,
        ),
    )

    # Create and register tools to toolkit
    toolkit = Toolkit()
    toolkit.register_tool_function(web_search)
    toolkit.register_tool_function(calculate)
    print()

    # Use async context manager for tool memory
    async with tool_memory:
        # Step 1: Record historical tool executions to memory
        await record_tool_history(tool_memory)

        # Step 2: Retrieve tool usage guidelines
        tool_names = ["web_search", "calculate"]
        tool_guidelines = await retrieve_tool_guidelines(
            tool_memory,
            tool_names,
        )

        # Step 3: Use ReActAgent with enhanced system prompt
        await use_react_agent_with_tool_memory(
            toolkit,
            tool_guidelines,
        )

    print("=" * 70)
    print("Workflow Complete!")
    print("=" * 70)
    print()
    print("Summary:")
    print("✓ Mock tools created and registered to Toolkit")
    print("✓ Historical tool executions recorded to tool memory")
    print("✓ Tool usage guidelines retrieved from memory")
    print("✓ ReActAgent system prompt enhanced with guidelines")
    print(
        "✓ ReActAgent successfully used tools with memory guidance",
    )
    print()
    print("Benefits:")
    print("- Agent learns from past tool usage patterns")
    print("- Reduced errors by following proven guidelines")
    print("- Better tool parameter selection")
    print("- Improved success rate over time")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
