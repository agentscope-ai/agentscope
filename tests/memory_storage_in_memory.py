# -*- coding: utf-8 -*-
""""""
from unittest import IsolatedAsyncioTestCase

from agentscope.memory import InMemoryMemoryStorage
from agentscope.message import Msg


class InMemoryStorageTest(IsolatedAsyncioTestCase):
    """The test case for in-memory memory storage."""

    async def asyncSetUp(self) -> None:
        """Set up the test case."""
        self.msg1 = Msg("user", "123", "user")
        self.msg2 = Msg("system", "789", "system")
        self.msg3 = Msg("assistant", "456", "assistant")

        self.msgs = [self.msg1, self.msg2, self.msg3]

    async def test_basic_operations(self) -> None:
        """Test adding messages to the storage."""
        storage = InMemoryMemoryStorage()

        # Specify msg IDs
        for i, msg in enumerate(self.msgs):
            msg.id = f"msg{i}"

        # Step1: Store messages in two steps
        await storage.add_message(self.msgs[:1])
        await storage.add_message(self.msgs[1:])

        # Retrieve messages
        msgs = await storage.get_messages()

        # Assert the messages are stored correctly
        self.assertEqual(
            3,
            await storage.size(),
        )
        self.assertEqual(3, len(msgs))
        self.assertListEqual(
            ["123", "789", "456"],
            [msg.content for msg in msgs],
        )
        self.assertListEqual(
            ["user", "system", "assistant"],
            [msg.role for msg in msgs],
        )
        self.assertListEqual(
            [f"msg{i}" for i in range(3)],
            [msg.id for msg in msgs],
        )

        # Step2: Remove messages by mark
        cnt = await storage.remove_messages(msg_ids=["msg1"])
        self.assertEqual(1, cnt)

        # Assert remaining messages
        msgs = await storage.get_messages()
        self.assertEqual(2, await storage.size())
        self.assertEqual(2, len(msgs))
        self.assertListEqual(
            ["123", "456"],
            [msg.content for msg in msgs],
        )

        # Step3: clear all messages
        await storage.clear()

        msgs = await storage.get_messages()
        self.assertEqual(0, await storage.size())
        self.assertEqual(0, len(msgs))


    async def test_mark_operations(self) -> None:
        """Test adding messages with marks to the storage."""
        storage = InMemoryMemoryStorage()

        res = await storage.update_messages_mark(
            new_mark="compression",
            msg_ids=["msg1", "msg2"],
        )

        # Assert no messages updated
        self.assertEqual(0, res)




        