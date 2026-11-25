# -*- coding: utf-8 -*-
"""Assistant-role media in the formatters whose API rejects it.

The chat endpoints accept media in user messages only. A ``DataBlock``
carried by an assistant message is skipped there, with a warning, rather
than promoted into a user message: promoting would change the role of the
media and add a conversation turn (#3965), and ``DeepSeekChatFormatter``
pins the same skip.
"""
from unittest import IsolatedAsyncioTestCase

from agentscope.formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
    DashScopeChatFormatter,
    MiniMaxChatFormatter,
    MoonshotChatFormatter,
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
    OpenAIResponseFormatter,
    VolcengineChatFormatter,
)
from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    TextBlock,
    UserMsg,
)

_IMAGE_B64 = "ZmFrZQ=="

# Each of these emits an assistant message that its API only accepts
# text (and tool calls) in.
_STRICT_CHAT_FORMATTERS = [
    AnthropicChatFormatter,
    DashScopeChatFormatter,
    MiniMaxChatFormatter,
    MoonshotChatFormatter,
    OpenAIChatFormatter,
    OpenAIResponseFormatter,
    VolcengineChatFormatter,
]

# These fold every agent's content into user messages, where media is
# accepted, so assistant-role media must survive.
_FOLDING_MULTI_AGENT_FORMATTERS = [
    AnthropicMultiAgentFormatter,
    OpenAIMultiAgentFormatter,
]


def _image_block() -> DataBlock:
    return DataBlock(
        source=Base64Source(data=_IMAGE_B64, media_type="image/png"),
    )


class AssistantMediaSkipTest(IsolatedAsyncioTestCase):
    """Assistant-role media is skipped where the API rejects it."""

    async def test_assistant_media_is_skipped(self) -> None:
        """The media is dropped, the text stays, and a warning is logged."""
        msg = AssistantMsg(
            name="assistant",
            content=[TextBlock(text="look"), _image_block()],
        )

        for formatter_class in _STRICT_CHAT_FORMATTERS:
            with self.subTest(formatter=formatter_class.__name__):
                with self.assertLogs("as", level="WARNING") as logs:
                    res = await formatter_class().format([msg])

                self.assertEqual(len(res), 1)
                self.assertEqual(res[0]["role"], "assistant")
                self.assertNotIn(_IMAGE_B64, repr(res))
                self.assertIn("look", repr(res))
                self.assertTrue(
                    any("assistant" in message for message in logs.output),
                    logs.output,
                )

    async def test_user_media_is_kept(self) -> None:
        """Media in a user message is formatted as before."""
        msg = UserMsg(
            name="user",
            content=[TextBlock(text="look"), _image_block()],
        )

        for formatter_class in _STRICT_CHAT_FORMATTERS:
            with self.subTest(formatter=formatter_class.__name__):
                res = await formatter_class().format([msg])

                self.assertEqual(len(res), 1)
                self.assertIn(_IMAGE_B64, repr(res))

    async def test_folding_multi_agent_formatter_keeps_media(self) -> None:
        """Multi-agent formatters carry the media inside a user message."""
        msgs = [
            UserMsg(name="user", content="hi"),
            AssistantMsg(
                name="assistant",
                content=[TextBlock(text="look"), _image_block()],
            ),
        ]

        for formatter_class in _FOLDING_MULTI_AGENT_FORMATTERS:
            with self.subTest(formatter=formatter_class.__name__):
                res = await formatter_class().format(msgs)

                self.assertIn(_IMAGE_B64, repr(res))
