# -*- coding: utf-8 -*-
"""Tests for the fallback chat model token estimation."""
from unittest.mock import patch
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.model import ModelCard
from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    URLSource,
    UserMsg,
)


def _model_card(name: str, input_types: list[str]) -> ModelCard:
    """Build a minimal model card for token-estimation tests."""
    return ModelCard(
        name=name,
        label=name,
        status="active",
        input_types=input_types,
        output_types=["text/plain"],
        context_size=1000,
        output_size=100,
        parameter_schema={},
        parameters_overrides={},
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

    async def test_text_only_model_does_not_count_data_blocks(self) -> None:
        """Media discarded by a known text-only model costs no tokens."""
        with patch.object(
            MockModel,
            "list_models",
            return_value=[_model_card("text-only", ["text/plain"])],
        ):
            model = MockModel(model="text-only")
            tokens = await model.count_tokens(
                [
                    UserMsg(
                        name="user",
                        content=[
                            TextBlock(text="hi"),
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

        self.assertEqual(tokens, 1)

    async def test_model_only_counts_supported_media_types(self) -> None:
        """A known model only counts media types declared by its card."""
        with patch.object(
            MockModel,
            "list_models",
            return_value=[
                _model_card("vision", ["text/plain", "image/*"]),
            ],
        ):
            model = MockModel(model="vision")
            tokens = await model.count_tokens(
                [
                    UserMsg(
                        name="user",
                        content=[
                            DataBlock(
                                source=Base64Source(
                                    data="image",
                                    media_type="image/png",
                                ),
                            ),
                            DataBlock(
                                source=Base64Source(
                                    data="document",
                                    media_type="application/pdf",
                                ),
                            ),
                        ],
                    ),
                ],
                None,
            )

        self.assertEqual(tokens, 2000)

    async def test_model_card_lookup_is_cached(self) -> None:
        """Repeated capability checks do not repeatedly scan model cards."""
        image = DataBlock(
            source=Base64Source(
                data="image",
                media_type="image/png",
            ),
        )
        with patch.object(
            MockModel,
            "list_models",
            return_value=[
                _model_card("vision", ["text/plain", "image/*"]),
            ],
        ) as list_models:
            model = MockModel(model="vision")
            self.assertTrue(model.accepts_data_block(image))
            self.assertTrue(model.accepts_data_block(image))

        list_models.assert_called_once_with()
