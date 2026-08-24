# -*- coding: utf-8 -*-
"""Tests for the fallback chat model token estimation."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, ContextConfig, InjectionConfig
from agentscope.message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    URLSource,
    UserMsg,
)
from agentscope.model import ChatResponse, StructuredResponse
from agentscope.state import AgentState
from agentscope.tool import Toolkit


class CompressionAwareMockModel(MockModel):
    """Record provider calls while using the fallback token estimator."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize a non-streaming model with provider-call recording."""
        super().__init__(
            *args,
            stream=False,
            use_fallback_token_estimate=True,
            **kwargs,
        )
        self.provider_messages: list[list[Msg]] = []
        self.reject_dense_context: str | None = None

    async def _call_api(
        self,
        model_name: str,
        **kwargs: Any,
    ) -> ChatResponse:
        """Record the messages that reach the provider boundary."""
        del model_name
        messages = kwargs["messages"]
        self.provider_messages.append(messages)
        if self.reject_dense_context:
            provider_text = "".join(
                block.text
                for msg in messages
                for block in msg.get_content_blocks("text")
            )
            if self.reject_dense_context in provider_text:
                raise RuntimeError("context_length_exceeded")
        return ChatResponse(
            content=[TextBlock(text="provider response")],
            is_last=True,
        )


class LegacyCompressionAwareMockModel(CompressionAwareMockModel):
    """Record provider calls with the pre-PR bytes/4 fallback."""

    def _estimate_text_tokens(self, text: str) -> int:
        """Use the legacy estimate for the regression comparison."""
        return int(len(text.encode("utf-8")) / 4 + 0.5)


class DropOldestMockModel(CompressionAwareMockModel):
    """Fail the first oversized summary request containing the oldest item."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize structured-output call recording."""
        super().__init__(*args, **kwargs)
        self.structured_messages: list[list[Msg]] = []
        self.oldest_marker = ""

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list[Msg],
        structured_model: Any,
        **kwargs: Any,
    ) -> StructuredResponse:
        """Reject the first compression request that still has the oldest."""
        self.structured_messages.append(messages)
        text = "".join(
            block.text
            for msg in messages
            for block in msg.get_content_blocks("text")
        )
        if self.oldest_marker and self.oldest_marker in text:
            self.oldest_marker = ""
            raise RuntimeError("context_length_exceeded")
        return await super()._call_api_with_structured_output(
            model_name,
            messages,
            structured_model,
            **kwargs,
        )


class ModelCountTokensTest(IsolatedAsyncioTestCase):
    """Test the base chat model token estimation behavior."""

    async def asyncSetUp(self) -> None:
        """Set up a mock model that uses ChatModelBase.count_tokens."""
        self.model = MockModel(use_fallback_token_estimate=True)

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

    async def test_natural_ascii_keeps_original_estimate(self) -> None:
        """Natural English must not be inflated by the fallback bound."""
        text = "This is ordinary English prose with natural punctuation."
        tokens = await self.model.count_tokens(
            [UserMsg(name="user", content=[TextBlock(text=text)])],
            None,
        )

        self.assertEqual(tokens, 14)

    async def test_dense_text_and_non_ascii_are_not_underestimated(
        self,
    ) -> None:
        """Numeric, structured, and CJK text use denser fallback estimates."""
        texts_and_expected = [
            (
                "SELECT order_id, sum(amount * (1 - discount)) "
                "FROM orders WHERE customer_id = 88392019482;",
                37,
            ),
            ("上下文压缩不应因词数低估而跳过。", 16),
            (
                ("上下文压缩不应因词数低估而跳过。" * 3)[:44],
                44,
            ),
            ('{"amount":10000.50,"customer_id":88392019482}', 28),
            (
                'Review 中文 JSON: {"金额": 12345.67, "status": "ok"}',
                28,
            ),
        ]

        for text, expected in texts_and_expected:
            with self.subTest(text=text):
                tokens = await self.model.count_tokens(
                    [UserMsg(name="user", content=[TextBlock(text=text)])],
                    None,
                )

                baseline = (len(text.encode("utf-8")) + 2) // 4
                self.assertEqual(tokens, expected)
                self.assertGreaterEqual(tokens, baseline)

        english = "This is ordinary English prose with natural punctuation."
        cjk = texts_and_expected[1][0]
        self.assertLess(
            await self.model.count_tokens(
                [UserMsg(name="user", content=[TextBlock(text=english)])],
                None,
            ),
            len(english.encode("utf-8")),
        )
        self.assertGreater(
            await self.model.count_tokens(
                [UserMsg(name="user", content=[TextBlock(text=cjk)])],
                None,
            ),
            (len(cjk.encode("utf-8")) + 2) // 4,
        )

    async def test_reported_payloads_bound_tokenizer_counts(self) -> None:
        """The issue's exact payloads stay above their pure-text counts."""
        english = (
            "Please analyze the quarterly revenue trends, cost breakdown, "
            "and operating profit margin across all departments for Q3 "
            "2026."
        )
        chinese = (
            "请分析2026年第三季度集团财务营收趋势、成本结构明细"
            "以及各事业部的营业利润率情况。"
        )
        sql = (
            "SELECT order_id, sum(amount * (1 - discount)) as net_total, "
            "count(item_id) as total_items FROM orders WHERE amount > "
            "10000.50 AND customer_id IN (88392019482, 88392019483, "
            "88392019484)   AND create_time >= '2026-01-01 00:00:00' "
            "AND status IN ('PAID', 'SETTLED', 'DELIVERED') GROUP BY "
            "order_id HAVING net_total > 500000.00 ORDER BY net_total "
            "DESC LIMIT 100;"
        )
        json_result = (
            '{"status": "success", "code": 200, "data": ['
            '{"order_id": 1001, "amount": 15200.5, "customer_id": '
            '88392019482, "status": "PAID"}, {"order_id": 1002, '
            '"amount": 83400.0, "customer_id": 88392019483, '
            '"status": "SETTLED"}], "summary": {"total_count": 2, '
            '"sum_amount": 98600.5}}'
        )
        mixed = "".join(
            [
                "请帮我查询 2026 年 Q3 大额订单及结算状态。",
                "好的，我将调用数据库执行 SQL 查询：SELECT * FROM orders "
                "WHERE amount > 50000;",
                '返回结果如下：{"orders": [{"id": 1, "amount": '
                '62000.0, "status": "SETTLED"}], "total": 1}',
                "已获取到 1 笔大额订单，金额为 62,000.00 元，状态为已结算。"
                "请问还需要进一步汇总分析吗？",
            ],
        )
        payloads = [
            (english, 26, 33),
            (chinese, 24, 41),
            (sql, 149, 149),
            (json_result, 134, 151),
            (mixed, 136, 143),
        ]

        for text, tokenizer_tokens, expected in payloads:
            with self.subTest(text=text[:40]):
                estimate = await self.model.count_tokens(
                    [UserMsg(name="user", content=text)],
                    None,
                )
                self.assertEqual(estimate, expected)
                self.assertGreaterEqual(estimate, tokenizer_tokens)
                self.assertLessEqual(estimate, tokenizer_tokens * 2)

    async def _run_compression_flow(
        self,
        model: CompressionAwareMockModel,
        old_context: str,
        context_size: int,
        context: list[Msg] | None = None,
        reserve_ratio: float = 0.1,
    ) -> str:
        """Run one reply and return the text sent to the provider."""
        model.context_size = context_size
        model.set_structured_response(
            StructuredResponse(
                content={
                    "task_overview": "Keep the active task.",
                    "current_state": "The old context was summarized.",
                    "important_discoveries": "The context is token dense.",
                    "next_steps": "Continue with the latest user input.",
                    "context_to_preserve": "Preserve the user requirements.",
                },
            ),
        )
        agent = Agent(
            name="Friday",
            system_prompt="Be concise.",
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=reserve_ratio,
            ),
            toolkit=Toolkit(),
            state=AgentState(
                session_id="compression-regression",
                context=context or [UserMsg(name="user", content=old_context)],
            ),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

        await agent.reply(UserMsg(name="user", content="Continue."))

        self.assertEqual(len(model.provider_messages), 1)
        return "".join(
            block.text
            for msg in model.provider_messages[0]
            for block in msg.get_content_blocks("text")
        )

    async def test_compression_happens_before_provider_call(self) -> None:
        """Each issue-shaped input is compressed before the provider call."""
        scenarios = [
            (
                "sql",
                "SELECT order_id, sum(amount * (1 - discount)) "
                "as net_total FROM orders WHERE amount > 10000.50 "
                "AND customer_id = 88392019482 GROUP BY order_id "
                "HAVING net_total > 500000.00;",
                68,
            ),
            (
                "numeric",
                "1234567890 9876543210 1122334455 9988776655",
                40,
            ),
            (
                "cjk",
                ("上下文压缩不应因词数低估而跳过。" * 3)[:44],
                54,
            ),
            (
                "json",
                '{"amount":10000.50,"customer_id":88392019482,'
                '"status":"paid","items":[{"sku":"A-1024","qty":17}]}',
                60,
            ),
            (
                "mixed",
                'Review 中文 JSON: {"金额": 12345.67, "status": "ok"}',
                35,
            ),
        ]

        for name, old_context, context_size in scenarios:
            with self.subTest(name=name):
                legacy_model = LegacyCompressionAwareMockModel()
                legacy_model.reject_dense_context = old_context
                with self.assertRaisesRegex(
                    RuntimeError,
                    "context_length_exceeded",
                ):
                    await self._run_compression_flow(
                        legacy_model,
                        old_context,
                        context_size,
                    )

                new_model = CompressionAwareMockModel()
                new_model.reject_dense_context = old_context
                provider_text = await self._run_compression_flow(
                    new_model,
                    old_context,
                    context_size,
                )
                self.assertIn("The old context was summarized.", provider_text)
                self.assertNotIn(old_context, provider_text)

    async def test_drop_oldest_retries_real_overflow(self) -> None:
        """A real overflow retry removes the oldest context before retrying."""
        oldest_marker = "OLDEST_DROP_OLDEST_MARKER"
        second_marker = "SECOND_DROP_OLDEST_MARKER"
        oldest = oldest_marker + ('{"amount":10000.50},' * 100)
        second = second_marker + ("9876543210," * 4)
        recent = "recent context " * 20

        model = DropOldestMockModel()
        model.oldest_marker = oldest_marker
        provider_text = await self._run_compression_flow(
            model,
            oldest,
            context_size=1200,
            context=[
                UserMsg(name="user", content=oldest),
                UserMsg(name="user", content=second),
                UserMsg(name="user", content=recent),
            ],
            reserve_ratio=0.08,
        )

        self.assertEqual(len(model.structured_messages), 2)
        first_text = "".join(
            block.text
            for msg in model.structured_messages[0]
            for block in msg.get_content_blocks("text")
        )
        second_text = "".join(
            block.text
            for msg in model.structured_messages[1]
            for block in msg.get_content_blocks("text")
        )
        self.assertIn(oldest_marker, first_text)
        self.assertIn(second_marker, first_text)
        self.assertNotIn(oldest_marker, second_text)
        self.assertIn(second_marker, second_text)
        self.assertNotIn(oldest_marker, provider_text)
        self.assertNotIn(second_marker, provider_text)
        self.assertNotIn(oldest, provider_text)

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
