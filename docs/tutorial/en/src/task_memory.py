# -*- coding: utf-8 -*-
"""
.. _memory:

Memory
========================

The memory module in AgentScope is responsible for

- storing the messages and
- managing them with specific marks
in different storage implementations.

The **mark** is a string label associated with each message in the memory,
which can be used to categorize, filter, and retrieve messages based on their
context or purpose.

It's powerful for high-level memory management in agents. For example,
In `ReActAgent` class, the hint messages are stored with the
mark "hint", and the memory compression functionality is also implemented
based on marks.

.. note:: The memory module only provides storage and management
 functionalities. The algorithm logic such as compression is implemented in
 the agent level.

Currently, AgentScope provides the following memory storage implementations:

.. list-table:: The built-in memory storage implementations in AgentScope
    :header-rows: 1

    * - Memory Class
      - Description
    * - ``InMemoryMemory``
      - A simple in-memory implementation of memory storage.
    * - ``AsyncSQLAlchemyMemory``
      - An asynchronous SQLAlchemy-based implementation of memory storage, which supports various databases such as SQLite, PostgreSQL, MySQL, etc.
    * - ``RedisMemory``
      - A Redis-based implementation of memory storage.

.. tip:: If you're interested in contributing new memory storage implementations, please refer to the
 `Contribution Guide <https://github.com/agentscope-ai/agentscope/blob/main/CONTRIBUTING.md#types-of-contributions>`_.

All the above memory classes inherit from the base class ``MemoryBase``, and
provide the following methods to manage the messages in the memory:

.. list-table:: The methods provided by the memory classes
    :header-rows: 1

    * - Method
      - Description
    * - ``add(
            memories: Msg | list[Msg] | None,
            marks: str | list[str] | None = None,
        ) -> None``
      - Add ``Msg`` object(s) to the memory storage with the given mark(s) (if provided).
    * - ``delete(msg_ids: list[str]) -> int``
      - Delete messages from the memory storage by their IDs.
    * - ``delete_by_mark(mark: str | list[str]) -> int``
      - Delete messages from the memory by their marks.
    * - ``size() -> int``
        - Get the size of the memory storage.
    * - ``clear() -> None``
      - Clear the memory storage.
    * - ``get_memory(
            mark: str | None = None,
            exclude_mark: str | None = None,
        ) -> list[Msg]``
      - Get the messages from the memory by mark (if provided). Otherwise, get all messages. If the ``update_compressed_summary`` method is used to store a compressed summary, it will be attached to the head of the returned messages.
    * - ``update_messages_mark(
            new_mark: str | None,
            old_mark: str | None = None,
            msg_ids: list[str] | None = None,
        ) -> int``
      - A unified method to update marks of messages in the storage (add, remove, or change marks).
    * - ``update_compressed_summary(
            summary: str,
        ) -> None``
      - Update the summary attribute stored in the memory.
"""
import asyncio
import json

import fakeredis
from sqlalchemy.ext.asyncio import create_async_engine

from agentscope.memory import (
    InMemoryMemory,
    AsyncSQLAlchemyMemory,
    RedisMemory,
)
from agentscope.message import Msg


# %%
# In-Memory Memory
# ~~~~~~~~~~~~~~~~~~~~~~~~
#
# The in-memory memory provides a simple way to store messages in memory.
# Together with the :ref:`state` module, it can persist the memory content across
# different users and sessions.


async def in_memory_example():
    """An example of using InMemoryMemory to store messages in memory."""
    memory = InMemoryMemory()
    await memory.add(
        Msg("Alice", "Generate a report about AgentScope", "user"),
    )

    # Add a hint message with the mark "hint"
    await memory.add(
        [
            Msg(
                "system",
                "<system-hint>Create a plan first to collect information and "
                "generate the report step by step.</system-hint>",
                "system",
            ),
        ],
        marks="hint",
    )

    msgs = await memory.get_memory(mark="hint")
    print("The messages with mark 'hint':")
    for msg in msgs:
        print(f"- {msg}")

    # All the stored messages can be exported and loaded via ``state_dict`` and ``load_state_dict`` methods.
    state = memory.state_dict()
    print("The state dict of the memory:")
    print(json.dumps(state, indent=2))

    # delete messages by mark
    deleted_count = await memory.delete_by_mark("hint")
    print(f"Deleted {deleted_count} messages with mark 'hint'.")

    print("The state dict of the memory after deletion:")
    state = memory.state_dict()
    print(json.dumps(state, indent=2))


asyncio.run(in_memory_example())

# %%
# Relational Database Memory
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope provides a unified interface to work with relational databases via SQLAlchemy, supporting
#
# - various databases such as SQLite, PostgreSQL, MySQL, etc.
# - user and session management, and
# - connection pooling in the production environment
#
# Specifically, here we use a memory backed by SQLite as an example.


async def sqlalchemy_example() -> None:
    """An example of using AsyncSQLAlchemyMemory to store messages in a SQLite database."""

    # Create an async SQLAlchemy engine first
    engine = create_async_engine("sqlite+aiosqlite:///./test_memory.db")

    # Then create the memory with the engine
    memory = AsyncSQLAlchemyMemory(
        engine_or_session=engine,
        # Optionally specify user_id and session_id
        user_id="user_1",
        session_id="session_1",
    )

    await memory.add(
        Msg("Alice", "Generate a report about AgentScope", "user"),
    )

    await memory.add(
        [
            Msg(
                "system",
                "<system-hint>Create a plan first to collect information and "
                "generate the report step by step.</system-hint>",
                "system",
            ),
        ],
        marks="hint",
    )

    msgs = await memory.get_memory(mark="hint")
    print("The messages with mark 'hint':")
    for msg in msgs:
        print(f"- {msg}")

    # Close the engine when done
    await memory.close()


asyncio.run(sqlalchemy_example())

# %%
# Optionally, you can also use the ``AsyncSQLAlchemyMemory`` as an async context manager, and the session will be closed automatically when exiting the context.


async def sqlalchemy_context_example() -> None:
    """Example of using AsyncSQLAlchemyMemory as an async context manager."""
    engine = create_async_engine("sqlite+aiosqlite:///./test_memory.db")
    async with AsyncSQLAlchemyMemory(
        engine_or_session=engine,
        user_id="user_1",
        session_id="session_1",
    ) as memory:
        await memory.add(
            Msg("Alice", "Generate a report about AgentScope", "user"),
        )

        msgs = await memory.get_memory()
        print("All messages in the memory:")
        for msg in msgs:
            print(f"- {msg}")


asyncio.run(sqlalchemy_context_example())

# %%
# In production environment e.g. with FastAPI, the connection pooling can be enabled as follows:
#
# .. code-block:: python
#    :caption: SQLAlchemy Memory with Connection Pooling in FastAPI
#
#    from typing import AsyncGenerator
#
#     from fastapi import FastAPI, Depends
#     from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
#
#     from agentscope.agent import ReActAgent
#     from agentscope.pipeline import stream_printing_messages
#
#
#     app = FastAPI()
#
#     # Create an async SQLAlchemy engine with connection pooling
#     engine = create_async_engine(
#         "sqlite+aiosqlite:///./test_memory.db",
#         pool_size=10,
#         max_overflow=20,
#         pool_timeout=30,
#         # ...  The other pool settings
#     )
#
#     # Create a session maker
#     async_session_marker = async_sessionmaker(
#         engine,
#         expire_on_commit=False,
#         autocommit=False,
#         autoflush=False,
#     )
#
#     async def get_db() -> AsyncGenerator[AsyncSession, None]:
#         async with async_session_marker() as session:
#             try:
#                 yield session
#                 await session.commit()
#             except Exception:
#                 await session.rollback()
#                 raise
#             finally:
#                 await session.close()
#
#     @app.post("/chat")
#     async def chat_endpoint(
#         user_id: str,
#         session_id: str,
#         input: str,
#         db_session: AsyncSession = Depends(get_db),
#     ):
#         # Some setup for the agent
#         ...
#
#         # Create the agent with the SQLAlchemy memory
#         agent = ReActAgent(
#             # ...
#             memory=AsyncSQLAlchemyMemory(
#                 engine_or_session=db_session,
#                 user_id=user_id,
#                 session_id=session_id,
#             ),
#         )
#
#         # Handle the chat with the agent
#         async for msg, _ in stream_printing_messages(
#             agents=[agent],
#             coroutine_task=agent(Msg("user", input, "user")),
#         ):
#             # yield msg to the client
#             ...
#
#
# NoSQL Database Memory
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope also provides memory implementations based on NoSQL databases such as Redis.
# It also supports user and session management, and connection pooling in the production environment.
#
# First, we can initialize the Redis memory as follows:


async def redis_memory_example() -> None:
    """An example of using RedisMemory to store messages in Redis."""
    # Use fakeredis for in-memory testing without a real Redis server
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    # Create the Redis memory
    memory = RedisMemory(
        # Using fake redis for demonstration
        connection_pool=fake_redis.connection_pool,
        # You can also connect to a real Redis server by specifying host and port
        # host="localhost",
        # port=6379,
        # Optionally specify user_id and session_id
        user_id="user_1",
        session_id="session_1",
    )

    # Add a message to the memory
    await memory.add(
        Msg(
            "Alice",
            "Generate a report about AgentScope",
            "user",
        ),
    )

    # Add a hint message with the mark "hint"
    await memory.add(
        Msg(
            "system",
            "<system-hint>Create a plan first to collect information and "
            "generate the report step by step.</system-hint>",
            "system",
        ),
        marks="hint",
    )

    # Retrieve messages with the mark "hint"
    msgs = await memory.get_memory(mark="hint")
    print("The messages with mark 'hint':")
    for msg in msgs:
        print(f"- {msg}")


asyncio.run(redis_memory_example())

# %%
# Similarly, the `RedisMemory` can also be used with connection pooling in the production environment, e.g., with FastAPI.
#
# .. code-block:: python
#    :caption: Redis Memory with Connection Pooling in FastAPI
#
#     from fastapi import FastAPI, HTTPException
#     from redis.asyncio import ConnectionPool
#     from contextlib import asynccontextmanager
#
#     # Global Redis connection pool
#     redis_pool: ConnectionPool | None = None
#
#
#     # Use the lifespan event to manage the Redis connection pool
#     @asynccontextmanager
#     async def lifespan(app: FastAPI):
#         global redis_pool
#         redis_pool = ConnectionPool(
#             host="localhost",
#             port=6379,
#             db=0,
#             password=None,
#             decode_responses=True,
#             max_connections=10,
#             encoding="utf-8",
#         )
#         print("✅ Redis connection established")
#
#         yield
#
#         await redis_pool.disconnect()
#         print("✅ Redis connection closed")
#
#
#     app = FastAPI(lifespan=lifespan)
#
#
#     @app.post("/chat_endpoint")
#     async def chat_endpoint(
#         user_id: str, session_id: str, input: str
#     ):  # ✅ 直接使用BaseModel
#         """A chat endpoint"""
#         global redis_pool
#         if redis_pool is None:
#             raise HTTPException(
#                 status_code=500,
#                 detail="Redis connection pool is not initialized.",
#             )
#
#         # Create the Redis memory
#         memory = RedisMemory(
#             connection_pool=redis_pool,
#             user_id=user_id,
#             session_id=session_id,
#         )
#
#         ...
#
#         # Close the Redis client connection when done
#         client = memory.get_client()
#         await client.aclose()
#
#
#
# Customizing Memory
# ~~~~~~~~~~~~~~~~~~~~~~~~
#
# To customize your own memory, just inherit from ``MemoryBase`` and implement the following methods:
#
# .. list-table::
#     :header-rows: 1
#
#     * - Method
#       - Description
#     * - ``add``
#       - Add ``Msg`` objects to the memory
#     * - ``delete``
#       - Delete ``Msg`` objects from the memory
#     * - ``delete_by_mark``
#       - Delete ``Msg`` objects from the memory by their marks
#     * - ``size``
#       - The size of the memory
#     * - ``clear``
#       - Clear the memory content
#     * - ``get_memory``
#       - Get the memory content as a list of ``Msg`` objects
#     * - ``update_messages_mark``
#       - Update marks of messages in the memory
#     * - ``state_dict``
#       - Get the state dictionary of the memory
#     * - ``load_state_dict``
#       - Load the state dictionary of the memory
#
# Further Reading
# ~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`agent`
# - :ref:`long-term-memory`
