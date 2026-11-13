# -*- coding: utf-8 -*-
"""Tests for the in-process cooperative steering source."""
from unittest import IsolatedAsyncioTestCase

from agentscope.message import UserMsg
from agentscope.middleware import SteeringQueue


class SteeringQueueTest(IsolatedAsyncioTestCase):
    """Acknowledgement must be all-or-nothing."""

    async def test_duplicate_acknowledgement_raises_without_committing(
        self,
    ) -> None:
        """A repeated input is rejected before any receipt is settled."""
        queue = SteeringQueue()
        await queue.start_reply("session", "reply")
        receipt = queue.submit(
            UserMsg(name="user", content="guide"),
            session_id="session",
            reply_id="reply",
        )
        item = (await queue.read("session", "reply"))[0]

        with self.assertRaises(LookupError):
            await queue.acknowledge("session", "reply", [item, item])

        self.assertEqual(receipt.status, "pending")
        self.assertEqual(await queue.read("session", "reply"), [item])
