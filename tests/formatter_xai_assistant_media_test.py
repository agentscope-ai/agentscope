# -*- coding: utf-8 -*-
"""Regression tests for assistant-authored media in the xAI formatter.

xAI accepts media on user turns only, so an assistant ``DataBlock`` has
to be promoted into a following user message.  It must not be dropped:
the model would then answer a follow-up as if it had never produced the
image.
"""

import base64
import unittest

import tests.formatter_xai_test  # noqa: F401  installs the xai_sdk stub

from agentscope.formatter import XAIChatFormatter
from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    TextBlock,
    UserMsg,
)

PNG_B64 = base64.b64encode(b"IMG_DATA").decode()
# The payload carries the encoded form, not the raw bytes.
B64_MARKER = "SU1HX0RBVEE="


class XAIAssistantMediaTest(unittest.IsolatedAsyncioTestCase):
    """Assistant media must reach the payload, after its own turn."""

    async def asyncSetUp(self) -> None:
        """Create the formatter and a reusable image block."""
        self.formatter = XAIChatFormatter()
        self.image = DataBlock(
            source=Base64Source(data=PNG_B64, media_type="image/png"),
        )

    def _has_image(self, payload: list) -> bool:
        """Whether the encoded image reached the payload at all."""
        return B64_MARKER in repr(payload)

    async def test_assistant_media_is_promoted_after_its_turn(self) -> None:
        """An assistant image becomes a user message after the turn."""
        payload = await self.formatter.format(
            [
                UserMsg(name="u", content=[TextBlock(text="draw")]),
                AssistantMsg(
                    name="a",
                    content=[TextBlock(text="Here is"), self.image],
                ),
                UserMsg(name="u", content=[TextBlock(text="explain")]),
            ],
        )

        self.assertTrue(self._has_image(payload))
        self.assertEqual(payload[0].role, "user")
        self.assertEqual(payload[1].role, "assistant")
        # The promoted media follows the assistant turn and precedes the
        # next user message, so the conversation keeps its order.
        self.assertEqual(payload[2].role, "user")
        self.assertIn(B64_MARKER, repr(payload[2]))
        self.assertEqual(payload[3], payload[-1])

    async def test_image_only_assistant_message_is_not_lost(self) -> None:
        """An assistant turn that is only an image must still be present."""
        payload = await self.formatter.format(
            [
                UserMsg(name="u", content=[TextBlock(text="draw")]),
                AssistantMsg(name="a", content=[self.image]),
            ],
        )

        self.assertEqual(len(payload), 2)
        self.assertTrue(self._has_image(payload))

    async def test_user_media_is_still_forwarded_inline(self) -> None:
        """The user path keeps its existing behaviour."""
        payload = await self.formatter.format(
            [
                UserMsg(
                    name="u",
                    content=[TextBlock(text="look"), self.image],
                ),
            ],
        )

        self.assertEqual(len(payload), 1)
        self.assertIn(B64_MARKER, repr(payload[0]))
