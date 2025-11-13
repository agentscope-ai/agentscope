"""
数据库连接管理

提供统一的数据库连接管理和事务处理功能。
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from datetime import datetime, timezone

import asyncpg
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from ..util.logger import get_logger
from ..util.graceful_shutdown import get_registry

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy基类"""
    pass


class DatabaseManager:
    """数据库管理器

    提供数据库连接、会话管理和事务处理功能。
    """

    _instance: Optional["DatabaseManager"] = None

    def __new__(cls) -> "DatabaseManager":
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """初始化数据库管理器"""
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        self._connection_pool: Optional[asyncpg.pool.Pool] = None

        # 注册优雅关停
        get_registry().register(self.shutdown)

    async def initialize(
        self,
        database_url: str,
        pool_size: int = 10,
        max_overflow: int = 20,
        echo: bool = False,
    ) -> None:
        """初始化数据库连接

        Args:
            database_url: 数据库连接URL
            pool_size: 连接池大小
            max_overflow: 最大溢出连接数
            echo: 是否打印SQL语句
        """
        logger.info("初始化数据库连接...")

        try:
            # 创建SQLAlchemy异步引擎
            self._engine = create_async_engine(
                database_url,
                pool_size=pool_size,
                max_overflow=max_overflow,
                echo=echo,
                pool_pre_ping=True,
                poolclass=NullPool if database_url.startswith("sqlite") else None,
            )

            # 创建会话工厂
            self._session_factory = async_sessionmaker(
                bind=self._engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

            # 创建asyncpg连接池（用于直接asyncpg操作）
            if database_url.startswith("postgresql"):
                self._connection_pool = await asyncpg.create_pool(
                    database_url,
                    min_size=2,
                    max_size=pool_size,
                )

            # 测试连接
            await self._test_connection()

            logger.info("数据库连接初始化成功")

        except Exception as e:
            logger.error(f"数据库连接初始化失败: {e}")
            raise

    async def _test_connection(self) -> None:
        """测试数据库连接"""
        if self._engine:
            async with self._engine.begin() as conn:
                await conn.execute("SELECT 1")

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """获取数据库会话

        Yields:
            AsyncSession: 数据库会话
        """
        if self._session_factory is None:
            raise RuntimeError("数据库未初始化，请先调用initialize()")

        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[asyncpg.Connection, None]:
        """获取asyncpg连接

        Yields:
            asyncpg.Connection: asyncpg连接
        """
        if self._connection_pool is None:
            raise RuntimeError("连接池未初始化")

        async with self._connection_pool.acquire() as conn:
            yield conn

    async def execute_raw_sql(
        self,
        sql: str,
        *args,
        fetch: str = "all"  # "all", "one", "none", "val"
    ) -> Optional[list | dict | any]:
        """执行原生SQL查询

        Args:
            sql: SQL语句
            *args: SQL参数
            fetch: 获取方式 ("all", "one", "none", "val")

        Returns:
            查询结果
        """
        async with self.get_connection() as conn:
            if fetch == "all":
                return await conn.fetch(sql, *args)
            elif fetch == "one":
                return await conn.fetchrow(sql, *args)
            elif fetch == "none":
                return await conn.execute(sql, *args)
            elif fetch == "val":
                return await conn.fetchval(sql, *args)
            else:
                raise ValueError(f"不支持的fetch方式: {fetch}")

    async def close(self) -> None:
        """关闭数据库连接"""
        logger.info("正在关闭数据库连接...")

        if self._connection_pool:
            await self._connection_pool.close()
            self._connection_pool = None

        if self._engine:
            await self._engine.dispose()
            self._engine = None

        self._session_factory = None

        logger.info("数据库连接已关闭")

    async def shutdown(self) -> None:
        """优雅关停"""
        await self.close()

    @property
    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._engine is not None

    @property
    def engine(self) -> Optional[AsyncEngine]:
        """获取SQLAlchemy引擎"""
        return self._engine


# 全局数据库管理器实例
_database_manager: Optional[DatabaseManager] = None


def get_database_manager() -> DatabaseManager:
    """获取全局数据库管理器实例

    Returns:
        DatabaseManager: 数据库管理器实例
    """
    global _database_manager
    if _database_manager is None:
        _database_manager = DatabaseManager()
    return _database_manager


# 便捷函数
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话的便捷函数

    Yields:
        AsyncSession: 数据库会话
    """
    async with get_database_manager().get_session() as session:
        yield session


async def get_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """获取asyncpg连接的便捷函数

    Yields:
        asyncpg.Connection: asyncpg连接
    """
    async with get_database_manager().get_connection() as conn:
        yield conn