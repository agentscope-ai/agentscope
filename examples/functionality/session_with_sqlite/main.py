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


async def main(username: str, query: str) -> None:
    """Create an agent, load from session, chat with it, and save its state
    to SQLite.

    Args:
        username (`str`):
            The username to identify the session.
        query (`str`):
            The user input query.
    """

    agent = ReActAgent(
        name="friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        formatter=DashScopeChatFormatter(),
    )

    # Create the SQLite session
    session = SqliteSession(SQLITE_PATH)

    # Load the agent state by the given key "friday_of_user"
    # The load_session_state supports multiple state modules
    await session.load_session_state(
        session_id=username,
        friday_of_user=agent,
    )

    # Chat with it to generate some state
    await agent(
        Msg("user", query, "user"),
    )

    # Save the agent state by the given key "friday_of_user"
    # Also support multiple state modules (e.g. multiple agents)
    await session.save_session_state(
        session_id=username,
        friday_of_user=agent,
    )


print("User named Alice chats with the agent ...")
asyncio.run(main("alice", "What's the capital of America?"))

print("User named Bob chats with the agent ...")
asyncio.run(main("bob", "What's the capital of China?"))

print(
    "\nNow, let's recover the session for Alice and ask about what the user "
    "asked before.",
)
asyncio.run(
    main(
        "alice",
        "What did I ask you before, what's your answer and how many "
        "questions have I asked you?",
    ),
)
