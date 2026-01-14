# -*- coding: utf-8 -*-
"""
.. _memory:

记忆
========================

AgentScope 中的记忆模块负责：

- 存储消息对象（``Msg``）
- 利用标记（mark）管理消息

**标记** 是与记忆中每条消息关联的字符串标签，可用于根据消息的上下文或目的对消息进行分类、过滤和检索。
可用于实现进阶的记忆管理功能，例如在 `ReActAgent` 类中，使用``"hint"``标签标记一次性的提示消息，
以便在使用完成后将其从记忆中删除。

.. note:: AgentScope 中的记忆模块仅提供消息存储和管理的原子功能，记忆压缩等算法逻辑在 `智能体 <agent>`_ 中实现。

目前，AgentScope 提供以下记忆存储实现：

.. list-table:: AgentScope 中的内置记忆类
    :header-rows: 1

    * - 类
      - 描述
    * - ``InMemoryMemory``
      - 简单的内存记忆存储实现。
    * - ``AsyncSQLAlchemyMemory``
      - 基于异步 SQLAlchemy 的记忆存储实现，支持如 SQLite、PostgreSQL、MySQL 等多种关系数据库。
    * - ``RedisMemory``
      - 基于 Redis 的记忆存储实现。

.. tip:: 如果您有兴趣贡献新的记忆存储实现，请参考 `贡献指南 <https://github.com/agentscope-ai/agentscope/blob/main/CONTRIBUTING.md#types-of-contributions>`_。

以上所有记忆类均继承自基类 ``MemoryBase``，并提供以下方法来管理记忆中的消息：

.. list-table:: 记忆类提供的方法
    :header-rows: 1

    * - 方法
      - 描述
    * - ``add(
            memories: Msg | list[Msg] | None,
            marks: str | list[str] | None = None,
        ) -> None``
      - 将 ``Msg`` 对象添加到记忆存储中，并使用给定的标记（如果提供）。
    * - ``delete(msg_ids: list[str]) -> int``
      - 通过ID从记忆存储中删除消息。
    * - ``delete_by_mark(mark: str | list[str]) -> int``
      - 通过标记从记忆中删除消息。
    * - ``size() -> int``
        - 获取记忆存储的大小。
    * - ``clear() -> None``
      - 清空记忆存储。
    * - ``get_memory(
            mark: str | None = None,
            exclude_mark: str | None = None,
        ) -> list[Msg]``
      - 通过标记从记忆中获取消息（如果提供）。否则，获取所有消息。如果使用 ``update_compressed_summary`` 方法存储压缩摘要，它将附加到返回消息的头部。
    * - ``update_messages_mark(
            new_mark: str | None,
            old_mark: str | None = None,
            msg_ids: list[str] | None = None,
        ) -> int``
      - 统一的方法，用于更新存储中消息的标记（添加、删除或更改标记）。
    * - ``update_compressed_summary(
            summary: str,
        ) -> None``
      - 更新存储在记忆中的摘要属性。
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
# 内存记忆
# ~~~~~~~~~~~~~~~~~~~~~~~~
#
# 内存记忆提供了一种在内存中存储消息的简单方式。
# 结合 :ref:`state` 模块，它可以在不同用户和会话之间持久化记忆内容。
#


async def in_memory_example():
    """使用InMemoryMemory在内存中存储消息的示例。"""
    memory = InMemoryMemory()
    await memory.add(
        Msg("Alice", "生成一份关于AgentScope的报告", "user"),
    )

    # 添加一条带有标记"hint"的提示消息
    await memory.add(
        [
            Msg(
                "system",
                "<system-hint>首先创建一个计划来收集信息，然后逐步生成报告。</system-hint>",
                "system",
            ),
        ],
        marks="hint",
    )

    msgs = await memory.get_memory(mark="hint")
    print("带有标记'hint'的消息：")
    for msg in msgs:
        print(f"- {msg}")

    # 所有存储的消息都可以通过 ``state_dict`` 和 ``load_state_dict`` 方法导出和加载。
    state = memory.state_dict()
    print("记忆的状态字典：")
    print(json.dumps(state, indent=2, ensure_ascii=False))

    # 通过标记删除消息
    deleted_count = await memory.delete_by_mark("hint")
    print(f"删除了 {deleted_count} 条带有标记'hint'的消息。")

    print("删除后的记忆状态字典：")
    state = memory.state_dict()
    print(json.dumps(state, indent=2, ensure_ascii=False))


asyncio.run(in_memory_example())

# %%
# 关系数据库记忆（Relational Database Memory）
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope 通过 SQLAlchemy 提供统一的接口来使用关系数据库，支持：
#
# - 多种数据库，如 SQLite、PostgreSQL、MySQL 等
# - 用户和会话管理
# - 生产环境中的连接池
#
# 具体来说，这里我们以SQLite支持的记忆为例。


async def sqlalchemy_example() -> None:
    """使用 AsyncSQLAlchemyMemory 在 SQLite 数据库中存储消息的示例。"""

    # 首先创建一个异步 SQLAlchemy 引擎
    engine = create_async_engine("sqlite+aiosqlite:///./test_memory.db")

    # 然后使用该引擎创建记忆
    memory = AsyncSQLAlchemyMemory(
        engine_or_session=engine,
        # 可选传入指定user_id和session_id
        user_id="user_1",
        session_id="session_1",
    )

    await memory.add(
        Msg("Alice", "生成一份关于AgentScope的报告", "user"),
    )

    await memory.add(
        [
            Msg(
                "system",
                "<system-hint>首先创建一个计划来收集信息，然后逐步生成报告。</system-hint>",
                "system",
            ),
        ],
        marks="hint",
    )

    msgs = await memory.get_memory(mark="hint")
    print("带有标记'hint'的消息：")
    for msg in msgs:
        print(f"- {msg}")

    # 完成后关闭引擎
    await memory.close()


asyncio.run(sqlalchemy_example())

# %%
# 可选地，您也可以将 ``AsyncSQLAlchemyMemory`` 用作���步上下文管理器，退出上下文时会话将自动关闭。


async def sqlalchemy_context_example() -> None:
    """使用 AsyncSQLAlchemyMemory 作为异步上下文管理器的示例。"""
    engine = create_async_engine("sqlite+aiosqlite:///./test_memory.db")
    async with AsyncSQLAlchemyMemory(
        engine_or_session=engine,
        user_id="user_1",
        session_id="session_1",
    ) as memory:
        await memory.add(
            Msg("Alice", "生成一份关于 AgentScope 的报告", "user"),
        )

        msgs = await memory.get_memory()
        print("记忆中的所有消息：")
        for msg in msgs:
            print(f"- {msg}")


asyncio.run(sqlalchemy_context_example())

# %%
# 在生产环境中，例如使用FastAPI时，可以按如下方式启用连接池：
#
# .. code-block:: python
#    :caption: FastAPI中使用连接池的SQLAlchemy记忆
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
#     # 创建带连接池的异步SQLAlchemy引擎
#     engine = create_async_engine(
#         "sqlite+aiosqlite:///./test_memory.db",
#         pool_size=10,
#         max_overflow=20,
#         pool_timeout=30,
#         # ...  其他连接池设置
#     )
#
#     # 创建会话制造器
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
#         # 智能体的一些设置
#         ...
#
#         # 使用SQLAlchemy记忆创建智能体
#         agent = ReActAgent(
#             # ...
#             memory=AsyncSQLAlchemyMemory(
#                 engine_or_session=db_session,
#                 user_id=user_id,
#                 session_id=session_id,
#             ),
#         )
#
#         # 处理与智能体的对话
#         async for msg, _ in stream_printing_messages(
#             agents=[agent],
#             coroutine_task=agent(Msg("user", input, "user")),
#         ):
#             # 将消息返回给客户端
#             ...
#
#
# NoSQL数据库记忆（NoSQL Database Memory）
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope还提供基于NoSQL数据库（如Redis）的记忆实现。
# 它也支持用户和会话管理，以及生产环境中的连接池。
#
# 首先，我们可以按如下方式初始化Redis记忆：


async def redis_memory_example() -> None:
    """使用 RedisMemory 在 Redis 中存储消息的示例。"""
    # 使用fakeredis进行内存测试，无需真实的 Redis 服务器
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    # 创建 Redis 记忆
    memory = RedisMemory(
        # 使用fake redis进行演示
        connection_pool=fake_redis.connection_pool,
        # 也可以通过指定主机和端口连接到真实的Redis服务器
        # host="localhost",
        # port=6379,
        # 可选地指定 user_id 和 session_id
        user_id="user_1",
        session_id="session_1",
    )

    # 向记忆中添加消息
    await memory.add(
        Msg(
            "Alice",
            "生成一份关于AgentScope的报告",
            "user",
        ),
    )

    # 添加一条带有标记"hint"的提示消息
    await memory.add(
        Msg(
            "system",
            "<system-hint>首先创建一个计划来收集信息，然后逐步生成报告。</system-hint>",
            "system",
        ),
        marks="hint",
    )

    # 检索带有标记"hint"的消息
    msgs = await memory.get_memory(mark="hint")
    print("带有标记'hint'的消息：")
    for msg in msgs:
        print(f"- {msg}")


asyncio.run(redis_memory_example())

# %%
# 同样，`RedisMemory` 也可以在生产环境中使用连接池，例如与FastAPI一起使用。
#
# .. code-block:: python
#    :caption: FastAPI中使用连接池的Redis记忆
#
#     from fastapi import FastAPI, HTTPException
#     from redis.asyncio import ConnectionPool
#     from contextlib import asynccontextmanager
#
#     # 全局Redis连接池
#     redis_pool: ConnectionPool | None = None
#
#
#     # 使用lifespan事件管理Redis连接池
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
#         print("✅ Redis连接已建立")
#
#         yield
#
#         await redis_pool.disconnect()
#         print("✅ Redis连接已关闭")
#
#
#     app = FastAPI(lifespan=lifespan)
#
#
#     @app.post("/chat_endpoint")
#     async def chat_endpoint(
#         user_id: str, session_id: str, input: str
#     ):
#         """聊天端点"""
#         global redis_pool
#         if redis_pool is None:
#             raise HTTPException(
#                 status_code=500,
#                 detail="Redis连接池未初始化。",
#             )
#
#         # 创建Redis记忆
#         memory = RedisMemory(
#             connection_pool=redis_pool,
#             user_id=user_id,
#             session_id=session_id,
#         )
#
#         ...
#
#         # 完成后关闭Redis客户端连接
#         client = memory.get_client()
#         await client.aclose()
#
#
#
# 自定义记忆（Customizing Memory）
# ~~~~~~~~~~~~~~~~~~~~~~~~
#
# 要自定义您自己的记忆实现，只需从 ``MemoryBase`` 继承并实现以下方法：
#
# .. list-table::
#     :header-rows: 1
#
#     * - 方法
#       - 描述
#     * - ``add``
#       - 向记忆中添加 ``Msg`` 对象
#     * - ``delete``
#       - 从记忆中删除 ``Msg`` 对象
#     * - ``delete_by_mark``
#       - 通过标记从记忆中删除 ``Msg`` 对象
#     * - ``size``
#       - 记忆的大小
#     * - ``clear``
#       - 清空记忆内容
#     * - ``get_memory``
#       - 以 ``Msg`` 对象列表的形式获取记忆内容
#     * - ``update_messages_mark``
#       - 更新记忆中消息的标记
#     * - ``state_dict``
#       - 获取记忆的状态字典
#     * - ``load_state_dict``
#       - 加载记忆的状态字典
#
# 延伸阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`agent`
# - :ref:`long-term-memory`
