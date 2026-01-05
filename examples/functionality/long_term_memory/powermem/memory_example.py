# -*- coding: utf-8 -*-
"""PowerMem long-term memory example."""

import asyncio
import os
from dotenv import load_dotenv

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory, PowerMemLongTermMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import ToolResponse, Toolkit


load_dotenv()


def build_powermem_config() -> dict:
    """Build a powermem config for OceanBase."""
    embedding_dims = int(os.getenv("DASHSCOPE_EMBEDDING_DIMS", "1536"))
    dashscope_base_url = os.getenv("DASHSCOPE_BASE_URL")
    return {
        "llm": {
            "provider": "qwen",
            "config": {
                "api_key": os.getenv("DASHSCOPE_API_KEY"),
                "model": os.getenv("DASHSCOPE_LLM_MODEL", "qwen-max-latest"),
                "dashscope_base_url": dashscope_base_url,
            },
        },
        "embedder": {
            "provider": "qwen",
            "config": {
                "api_key": os.getenv("DASHSCOPE_API_KEY"),
                "model": os.getenv(
                    "DASHSCOPE_EMBEDDING_MODEL",
                    "text-embedding-v4",
                ),
                "embedding_dims": embedding_dims,
                "dashscope_base_url": dashscope_base_url,
            },
        },
        "vector_store": {
            "provider": "oceanbase",
            "config": {
                "host": os.getenv("OCEANBASE_HOST", "127.0.0.1"),
                "port": int(os.getenv("OCEANBASE_PORT", "2881")),
                "user": os.getenv("OCEANBASE_USER", "root"),
                "password": os.getenv("OCEANBASE_PASSWORD", ""),
                "db_name": os.getenv("OCEANBASE_DATABASE", "powermem"),
                "collection_name": os.getenv(
                    "OCEANBASE_COLLECTION",
                    "memories",
                ),
                "embedding_model_dims": embedding_dims,
            },
        },
        "intelligent_memory": {
            "enabled": True,
        },
    }


async def test_record_to_memory(
    memory: PowerMemLongTermMemory,
) -> None:
    """Test the record_to_memory tool function interface."""
    print("Interface 1: record_to_memory (Tool Function)")
    print("-" * 70)
    print("Purpose: Explicit memory recording with structured content")
    print("Test case: Recording user travel preferences...")

    result: ToolResponse = await memory.record_to_memory(
        thinking=("The user is sharing travel preferences and habits"),
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
    print(f"Result: {result_text}")
    print()


async def test_retrieve_from_memory(
    memory: PowerMemLongTermMemory,
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
    print("Retrieved memories:")
    print(retrieved_text or "No memories found")
    print()


async def test_record_direct(
    memory: PowerMemLongTermMemory,
) -> None:
    """Test the direct record method interface."""
    print("Interface 3: record (Direct Recording)")
    print("-" * 70)
    print("Purpose: Direct recording of message conversations")
    print("Test case: Recording work preferences and habits...")

    await memory.record(
        msgs=[
            Msg(
                role="user",
                content=(
                    "I work as a software engineer and prefer remote work"
                ),
                name="user",
            ),
            Msg(
                role="assistant",
                content=("Understood. You value remote work flexibility."),
                name="assistant",
            ),
            Msg(
                role="user",
                content="I start my day at 9 AM with a cup of coffee",
                name="user",
            ),
        ],
    )
    print("Status: Successfully recorded conversation messages")
    print()


async def test_retrieve_direct(
    memory: PowerMemLongTermMemory,
) -> None:
    """Test the direct retrieve method interface."""
    print("Interface 4: retrieve (Direct Retrieval)")
    print("-" * 70)
    print("Purpose: Query-based memory retrieval using messages")
    print()
    print("Test case: Querying work preferences...")

    memories = await memory.retrieve(
        msg=Msg(
            role="user",
            content="What do you know about my work preferences?",
            name="user",
        ),
    )
    print("Retrieved memories:")
    print(memories if memories else "No memories found")
    print()


async def test_react_agent_with_memory(
    memory: PowerMemLongTermMemory,
) -> None:
    """Test ReActAgent integration with PowerMem."""
    print("Interface 5: ReActAgent with PowerMem")
    print("-" * 70)
    print("Purpose: Demonstrate agent-driven memory operations")
    print()

    toolkit = Toolkit()
    agent = ReActAgent(
        name="Friday",
        sys_prompt=(
            "You are a helpful assistant with long-term memory. "
            "Record personal preferences using record_to_memory, "
            "and retrieve related memories before answering."
        ),
        model=DashScopeChatModel(
            model_name=os.getenv("DASHSCOPE_LLM_MODEL", "qwen-max-latest"),
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

    msg = Msg(
        role="user",
        content="When I travel to Hangzhou, I prefer to stay in a homestay",
        name="user",
    )
    msg = await agent(msg)
    print(f"Agent response: {msg.get_text_content()}\n")

    msg = Msg(
        role="user",
        content="What preference do I have?",
        name="user",
    )
    msg = await agent(msg)
    print(f"Agent response: {msg.get_text_content()}\n")


async def main() -> None:
    """Run PowerMem long-term memory examples."""
    long_term_memory = PowerMemLongTermMemory(
        config=build_powermem_config(),
        agent_name="Friday",
        user_name="user_123",
        run_name="session_001",
        infer=True,
    )

    await test_record_to_memory(long_term_memory)
    await test_retrieve_from_memory(long_term_memory)
    await test_record_direct(long_term_memory)
    await test_retrieve_direct(long_term_memory)
    await test_react_agent_with_memory(long_term_memory)


if __name__ == "__main__":
    asyncio.run(main())
