# -*- coding: utf-8 -*-
"""The main entry point for the session with SQLite example."""
import asyncio
import os

from sqlite_session import SqliteSession

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel

SQLITE_PATH = "./session.db"


async def save_session() -> None:
    """Create an agent, chat with it, and save its state to SQLite."""

    agent = ReActAgent(
        name="friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        formatter=DashScopeChatFormatter(),
    )

    # Chat with it to generate some state
    await agent(
        Msg("user", "What's the capital of America?", "user"),
    )

    # Save the session state by SqliteSession
    session = SqliteSession(SQLITE_PATH, "user_alice")

    # Save the agent state by the given key "friday_of_alice"
    # Also support multiple state modules (e.g. multiple agents)
    await session.save_session_state(friday_of_alice=agent)


async def load_session() -> None:
    """Create a new agent and load the previous state from SQLite."""

    agent = ReActAgent(
        name="friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        formatter=DashScopeChatFormatter(),
    )

    # Save the session state by SqliteSession
    session = SqliteSession(SQLITE_PATH, "user_alice")

    # Load the agent state by the given key "friday_of_alice"
    # The load_session_state supports multiple state modules
    await session.load_session_state(friday_of_alice=agent)

    # Continue the chat, the agent should remember the previous state
    await agent(
        Msg(
            "user",
            "What's my last question and your answer?",
            "user",
        ),
    )


print("Trying to create an agent and save its state to SQLite...")
asyncio.run(save_session())

print("\nTrying to create a new agent and load the state from SQLite...")
asyncio.run(load_session())
