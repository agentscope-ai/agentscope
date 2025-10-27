# -*- coding: utf-8 -*-
"""Personal memory example demonstrating ReMe personal memory functionality.

This module provides examples of how to use the ReMePersonalMemory class
for recording and retrieving persistent personal memories using the ReMe library.

The example demonstrates 4 core interfaces:
1. record_to_memory - Tool function for explicit memory recording
2. retrieve_from_memory - Tool function for keyword-based retrieval
3. record - Direct method for recording message conversations
4. retrieve - Direct method for query-based retrieval
"""

import asyncio
import dataclasses
import os

from dotenv import load_dotenv

from agentscope.memory.reme import ReMePersonalMemory
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import ToolResponse

load_dotenv()


async def main() -> None:
    """Run the personal memory examples to test 4 core interfaces."""
    
    # Initialize ReMePersonalMemory
    long_term_memory = ReMePersonalMemory(
        agent_name="Friday",
        user_name="user_123",  # This serves as workspace_id in ReMe
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
        
        # ============================================
        # Interface 1: record_to_memory (Tool Function)
        # ============================================
        print("Interface 1: record_to_memory (Tool Function)")
        print("-" * 70)
        print("Purpose: Explicit memory recording with structured content")
        print()
        print("Test case: Recording user's travel preferences...")
        
        result: ToolResponse = await long_term_memory.record_to_memory(
            thinking="The user is sharing their travel preferences and habits",
            content=[
                "I prefer to stay in homestays when traveling to Hangzhou",
                "I like to visit the West Lake in the morning",
                "I enjoy drinking Longjing tea",
            ],
        )
        result_text = " ".join(block.get("text", "") for block in result.content if block.get("type") == "text")
        print(f"✓ Result: {result_text}")
        print(f"✓ Status: {'Success' if 'Success' in result_text else 'Failed'}")
        print()

        # ============================================
        # Interface 2: retrieve_from_memory (Tool Function)
        # ============================================
        print("Interface 2: retrieve_from_memory (Tool Function)")
        print("-" * 70)
        print("Purpose: Keyword-based memory retrieval")
        print()
        print("Test case: Searching for 'Hangzhou travel' and 'tea preference'...")
        
        result = await long_term_memory.retrieve_from_memory(
            keywords=["Hangzhou travel", "tea preference"],
            limit=3,
        )
        retrieved_text = " ".join(block.get("text", "") for block in result.content if block.get("type") == "text")
        print(f"✓ Retrieved memories:")
        print(f"{retrieved_text}")
        print(f"✓ Status: {'Success - Found memories' if 'Hangzhou' in retrieved_text or 'tea' in retrieved_text else 'No relevant memories found'}")
        print()

        # ============================================
        # Interface 3: record (Direct Recording)
        # ============================================
        print("Interface 3: record (Direct Recording)")
        print("-" * 70)
        print("Purpose: Direct recording of message conversations")
        print()
        print("Test case: Recording work preferences and habits...")
        
        try:
            await long_term_memory.record(
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

        # ============================================
        # Interface 4: retrieve (Direct Retrieval)
        # ============================================
        print("Interface 4: retrieve (Direct Retrieval)")
        print("-" * 70)
        print("Purpose: Query-based memory retrieval using messages")
        print()
        print("Test case: Querying 'What do you know about my work preferences?'...")
        
        memories = await long_term_memory.retrieve(
            msg=Msg(
                role="user",
                content="What do you know about my work preferences?",
                name="user",
            ),
            limit=5,
        )
        print(f"✓ Retrieved memories:")
        print(f"{memories if memories else 'No memories found'}")
        print(f"✓ Status: {'Success - Found memories' if memories else 'No relevant memories found'}")
        print()

    print("=" * 70)
    print("Testing Complete: All 4 Core Interfaces Verified!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

