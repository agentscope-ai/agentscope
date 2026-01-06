# -*- coding: utf-8 -*-
""""""
from unittest import IsolatedAsyncioTestCase

from sqlalchemy import Engine, create_engine

from agentscope.memory import SQLAlchemyMemoryStorage


class SQLAlchemyStorageTest(IsolatedAsyncioTestCase):
    """The test case for SQLAlchemy storage."""

    async def test_add_messages(self) -> None:
        """Test adding messages to the storage."""
        engine = create_engine("sqlite:///example.db")

        storage = SQLAlchemyMemoryStorage(
            engine=engine,
        )
