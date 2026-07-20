# -*- coding: utf-8 -*-
"""Tests for the fallback chat model token estimation."""
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    URLSource,
    UserMsg,
)


class ModelCountTokensTest(IsolatedAsyncioTestCase):
    """Test the base chat model token estimation behavior."""

    async def asyncSetUp(self) -> None:
        """Set up a mock model that uses ChatModelBase.count_tokens."""
        self.model = MockModel()

    async def test_data_blocks_use_flat_multimodal_estimate(self) -> None:
        """Large base64 payloads are not counted as prompt text."""
        data = "a" * 400_000
        tokens = await self.model.count_tokens(
            [
                UserMsg(
                    name="user",
                    content=[
                        TextBlock(text="hi"),
                        DataBlock(
                            source=Base64Source(
                                data=data,
                                media_type="image/png",
                            ),
                        ),
                    ],
                ),
            ],
            None,
        )

        self.assertEqual(tokens, 2001)

    async def test_base64_and_url_data_blocks_have_same_estimate(self) -> None:
        """The same data block should not differ by source representation."""
        base64_tokens = await self.model.count_tokens(
            [
                UserMsg(
                    name="user",
                    content=[
                        DataBlock(
                            source=Base64Source(
                                data="a" * 400_000,
                                media_type="image/png",
                            ),
                        ),
                    ],
                ),
            ],
            None,
        )
        # The file does not need to exist; token estimation must not read
        # URLSource payloads.
        url_tokens = await self.model.count_tokens(
            [
                UserMsg(
                    name="user",
                    content=[
                        DataBlock(
                            source=URLSource(
                                url="file:///tmp/image.png",
                                media_type="image/png",
                            ),
                        ),
                    ],
                ),
            ],
            None,
        )

        self.assertEqual(base64_tokens, 2000)
        self.assertEqual(url_tokens, 2000)
