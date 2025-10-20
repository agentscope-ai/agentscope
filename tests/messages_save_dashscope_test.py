# -*- coding: utf-8 -*-
"""Tests for messages saving with DashScopeChatModel."""

import json
import os
import tempfile
from http import HTTPStatus
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import dashscope  # Ensure dashscope module is imported for patching
from agentscope.model import DashScopeChatModel


class TestMessagesSaveDashScope(IsolatedAsyncioTestCase):
    """Verify messages saving and forwarding (non-streaming)."""

    async def test_non_stream_collection(self) -> None:
        with patch("dashscope.aigc.generation.AioGeneration.call") as mock_call:
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "out.jsonl")
                model = DashScopeChatModel(
                    model_name="qwen-turbo",
                    api_key="k",
                    stream=False,
                    save_messages=True,
                    save_path=out,
                )

                # Build a DashScope-like response object
                response = Mock()
                response.status_code = HTTPStatus.OK
                response.output = Mock()
                response.output.choices = [Mock()]
                # Message must be dict-like supporting .get
                response.output.choices[0].message = {
                    "content": "I'll check the weather for you.",
                    "reasoning_content": "Let me think about this...",
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "function": {
                                "name": "get_weather",
                                "arguments": "{\"location\": \"Beijing\"}",
                            },
                        },
                    ],
                }
                response.usage = Mock()
                response.usage.input_tokens = 5
                response.usage.output_tokens = 10
                mock_call.return_value = response

                messages = [{"role": "user", "content": "What's the weather in Beijing?"}]
                tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "Get weather information",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "location": {"type": "string", "description": "City name"}
                                },
                                "required": ["location"]
                            }
                        }
                    }
                ]

                await model(messages, tools=tools, tool_choice="auto")

                # Verify JSONL written
                self.assertTrue(os.path.exists(out))
                with open(out, "r", encoding="utf-8") as f:
                    lines = [json.loads(l) for l in f.read().strip().splitlines()]
                self.assertEqual(len(lines), 1)
                rec = lines[0]
                self.assertIn("messages", rec)
                self.assertIn("tools", rec)
                self.assertIn("metadata", rec)
                msgs = json.loads(rec["messages"])  # stored as JSON string
                ts = json.loads(rec["tools"])  # stored as JSON string
                
                # Verify complete conversation includes assistant response
                self.assertEqual(len(msgs), 2)  # user message + assistant response
                self.assertEqual(msgs[0], messages[0])  # original user message
                self.assertEqual(msgs[1]["role"], "assistant")  # assistant response
                self.assertIn("content", msgs[1])  # assistant content
                self.assertIn("tool_calls", msgs[1])  # tool calls should be present
                
                # Verify content
                self.assertEqual(msgs[1]["content"], "I'll check the weather for you.")
                
                # Verify tool calls
                self.assertEqual(len(msgs[1]["tool_calls"]), 1)
                self.assertEqual(msgs[1]["tool_calls"][0]["function"]["name"], "get_weather")
                self.assertEqual(ts, tools)

    async def test_streaming_with_thinking_collection(self) -> None:
        """Test streaming response with thinking content."""
        with patch("dashscope.aigc.generation.AioGeneration.call") as mock_call:
            # Model will be instantiated inside tempdir to enable saving
            
            # Create mock streaming chunks
            chunks = [
                self._create_mock_chunk(
                    content="",
                    reasoning_content="Let me think about this step by step...",
                ),
                self._create_mock_chunk(
                    content="I'll help you with that.",
                    reasoning_content="",
                ),
            ]
            
            mock_call.return_value = self._create_async_generator(chunks)

            messages = [{"role": "user", "content": "Help me solve this problem"}]

            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "out.jsonl")
                model = DashScopeChatModel(
                    model_name="qwen-turbo", 
                    api_key="k", 
                    stream=True,
                    enable_thinking=True,
                    save_messages=True,
                    save_path=out,
                )

                # Consume the streaming response
                result = await model(messages)
                responses = []
                async for response in result:
                    responses.append(response)

                # Verify JSONL written
                self.assertTrue(os.path.exists(out))
                with open(out, "r", encoding="utf-8") as f:
                    lines = [json.loads(l) for l in f.read().strip().splitlines()]
                self.assertEqual(len(lines), 1)
                rec = lines[0]
                msgs = json.loads(rec["messages"])
                
                # Verify reasoning content is properly separated
                self.assertEqual(msgs[1]["role"], "assistant")
                self.assertIn("reasoning_content", msgs[1])
                self.assertIn("content", msgs[1])
                # The final response should have both reasoning and content
                self.assertTrue(msgs[1]["reasoning_content"])
                self.assertTrue(msgs[1]["content"])

    def _create_mock_chunk(
        self,
        content: str = "",
        reasoning_content: str = "",
        tool_calls: list = None,
    ):
        """Create a mock chunk for streaming responses."""
        from unittest.mock import Mock
        
        class _Msg:
            def __init__(self, content, reasoning_content, tool_calls):
                self.content = content
                self._d = {"reasoning_content": reasoning_content, "tool_calls": tool_calls or []}
            def get(self, k, default=None):
                return self._d.get(k, default)
            def __getitem__(self, k):
                return self._d[k]
        
        chunk = Mock()
        chunk.status_code = HTTPStatus.OK
        chunk.output = Mock()
        chunk.output.choices = [Mock()]
        # message needs .content attribute and .get() method
        chunk.output.choices[0].message = _Msg(content, reasoning_content, tool_calls)
        
        chunk.usage = Mock()
        chunk.usage.input_tokens = 5
        chunk.usage.output_tokens = 10
        return chunk

    async def _create_async_generator(self, items: list):
        """Create an asynchronous generator."""
        for item in items:
            yield item
