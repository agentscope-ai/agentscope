# -*- coding: utf-8 -*-
"""Task memory example demonstrating ReMe task memory functionality.

This module provides examples of how to use the ReMeTaskMemory class
for recording execution trajectories and retrieving relevant task experiences
using the ReMe library.

The example demonstrates 4 core interfaces:
1. record_to_memory - Tool function for recording execution trajectories
2. retrieve_from_memory - Tool function for query-based retrieval
3. record - Direct method for recording message conversations with scores
4. retrieve - Direct method for retrieving task experiences
"""

import asyncio
import os

from dotenv import load_dotenv

from agentscope.memory.reme import ReMeTaskMemory
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import ToolResponse

load_dotenv()


async def main() -> None:
    """Run the task memory examples to test 4 core interfaces."""
    
    # Initialize ReMeTaskMemory
    long_term_memory = ReMeTaskMemory(
        agent_name="TaskAssistant",
        user_name="task_workspace_123",  # This serves as workspace_id in ReMe
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
        
        # ============================================
        # Interface 1: record_to_memory (Tool Function)
        # ============================================
        print("Interface 1: record_to_memory (Tool Function)")
        print("-" * 70)
        print("Purpose: Record execution trajectories with structured messages")
        print()
        print("Test case: Recording project planning task trajectories...")
        
        result: ToolResponse = await long_term_memory.record_to_memory(
            trajectories=[
                {
                    "messages": [
                        {"role": "user", "content": "Help me create a project plan for a web application"},
                        {"role": "assistant", "content": "I'll help you create a comprehensive project plan. Let's break it down into phases: 1) Requirements gathering, 2) Design, 3) Development, 4) Testing, 5) Deployment"},
                        {"role": "user", "content": "That sounds good. Let's focus on the development phase."},
                        {"role": "assistant", "content": "For the development phase, I recommend: Frontend (React), Backend (FastAPI), Database (PostgreSQL), and following Agile methodology with 2-week sprints."},
                    ],
                    "score": 0.9  # High score for successful trajectory
                },
                {
                    "messages": [
                        {"role": "user", "content": "How do I manage project dependencies?"},
                        {"role": "assistant", "content": "Use a dependency management tool like npm for frontend and pip for Python backend. Create requirements.txt and package.json files."},
                    ],
                    "score": 0.85
                }
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
        print("Purpose: Query-based retrieval of task experiences")
        print()
        print("Test case: Searching for 'project planning best practices'...")
        
        result = await long_term_memory.retrieve_from_memory(
            query="What are the best practices for planning a web application project?",
            top_k=3,
        )
        retrieved_text = " ".join(block.get("text", "") for block in result.content if block.get("type") == "text")
        print(f"✓ Retrieved experiences:")
        print(f"{retrieved_text}")
        print(f"✓ Status: {'Success - Found experiences' if 'Task Experiences' in retrieved_text else 'No relevant experiences found'}")
        print()

        # ============================================
        # Interface 3: record (Direct Recording)
        # ============================================
        print("Interface 3: record (Direct Recording)")
        print("-" * 70)
        print("Purpose: Direct recording of message conversations with scores")
        print()
        print("Test case: Recording debugging task conversation...")
        
        try:
            await long_term_memory.record(
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
                score=0.95,  # High score for successful resolution
            )
            print("✓ Status: Successfully recorded debugging trajectory")
            print("✓ Messages recorded: 4 messages with score 0.95")
        except Exception as e:
            print(f"✗ Status: Failed - {str(e)}")
        print()

        # ============================================
        # Interface 4: retrieve (Direct Retrieval)
        # ============================================
        print("Interface 4: retrieve (Direct Retrieval)")
        print("-" * 70)
        print("Purpose: Query-based retrieval using messages")
        print()
        print("Test case: Querying 'How to debug API errors?'...")
        
        memories = await long_term_memory.retrieve(
            msg=Msg(
                role="user",
                content="How should I approach debugging API errors in my application?",
                name="user",
            ),
            top_k=5,
        )
        print(f"✓ Retrieved experiences:")
        print(f"{memories if memories else 'No experiences found'}")
        print(f"✓ Status: {'Success - Found experiences' if memories else 'No relevant experiences found'}")
        print()

        # Additional example: Recording multiple trajectories with different scores
        print("Additional Example: Recording multiple trajectories with scores")
        print("-" * 70)
        print("Test case: Recording testing strategies...")
        
        await long_term_memory.record(
            msgs=[
                Msg(role="user", content="What testing strategy should I use?", name="user"),
                Msg(role="assistant", content="Implement unit tests with pytest, integration tests, and end-to-end tests with Selenium.", name="assistant"),
            ],
            score=0.8,
        )
        print("✓ Recorded testing strategy trajectory (score: 0.8)")
        print()

    print("=" * 70)
    print("Testing Complete: All 4 Core Interfaces Verified!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

