# -*- coding: utf-8 -*-
"""Tests for validation of Chat API request payloads."""
from unittest import TestCase

from pydantic import ValidationError

from agentscope.app._router._schema._chat import ChatRequest
from agentscope.event import ExternalExecutionResultEvent
from agentscope.message import (
    Base64Source,
    DataBlock,
    ToolResultBlock,
    URLSource,
    UserMsg,
)


class ChatRequestTest(TestCase):
    """Validate chat request data-source boundaries."""

    def test_rejects_file_url_source(self) -> None:
        """A chat request must not cause the service to read a local file."""
        with self.assertRaisesRegex(ValidationError, "file://"):
            ChatRequest(
                agent_id="agent",
                session_id="session",
                input=UserMsg(
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
            )

    def test_accepts_remote_and_base64_sources(self) -> None:
        """Existing remote URLs and uploaded base64 content stay valid."""
        ChatRequest(
            agent_id="agent",
            session_id="session",
            input=UserMsg(
                name="user",
                content=[
                    DataBlock(
                        source=URLSource(
                            url="https://example.com/image.png",
                            media_type="image/png",
                        ),
                    ),
                    DataBlock(
                        source=Base64Source(
                            data="aGVsbG8=",
                            media_type="image/png",
                        ),
                    ),
                ],
            ),
        )

    def test_rejects_file_url_in_external_tool_result(self) -> None:
        """External tool results cannot introduce a service-local path."""
        with self.assertRaisesRegex(ValidationError, "file://"):
            ChatRequest(
                agent_id="agent",
                session_id="session",
                input=ExternalExecutionResultEvent(
                    reply_id="reply",
                    execution_results=[
                        ToolResultBlock(
                            id="tool-call",
                            name="external-tool",
                            output=[
                                DataBlock(
                                    source=URLSource(
                                        url="file:///tmp/image.png",
                                        media_type="image/png",
                                    ),
                                ),
                            ],
                        ),
                    ],
                ),
            )
