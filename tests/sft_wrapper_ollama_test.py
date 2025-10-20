# -*- coding: utf-8 -*-
"""Tests for ChatModelSFTWrapper with OllamaChatModel."""

import json
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import ollama  # Ensure ollama module is imported for patching
from agentscope.model import OllamaChatModel
from agentscope.sft import SFTDataCollector, ChatModelSFTWrapper


class TestSFTWrapperOllama(IsolatedAsyncioTestCase):
    """Verify wrapper writes JSONL and forwards results (non-streaming)."""

    async def test_non_stream_collection(self) -> None:
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OllamaChatModel(model_name="llama3.2", stream=False)
            model.client = mock_client

            # Mock response following Ollama response structure
            from unittest.mock import Mock
            response = AsyncMock()
            message = Mock()
            message.thinking = None
            message.content = "I'll check the weather for you."
            
            tool_call = Mock()
            tool_call.function = Mock()
            tool_call.function.name = "get_weather"
            tool_call.function.arguments = {"location": "Beijing"}
            message.tool_calls = [tool_call]
            
            response.message = message
            response.done = True
            response.prompt_eval_count = 1
            response.eval_count = 1

            mock_client.chat = AsyncMock(return_value=response)

            messages = [{"role": "user", "content": "hi"}]
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {"type": "object"},
                    },
                },
            ]

            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "out.jsonl")
                collector = SFTDataCollector(output_path=out, enable_collection=True)
                wrapped = ChatModelSFTWrapper(base_model=model, collector=collector)

                await wrapped(messages, tools=tools)

                self.assertTrue(os.path.exists(out))
                with open(out, "r", encoding="utf-8") as f:
                    lines = [json.loads(l) for l in f.read().strip().splitlines()]
                self.assertEqual(len(lines), 1)
                rec = lines[0]
                msgs = json.loads(rec["messages"])  # stored as JSON string
                ts = json.loads(rec["tools"])  # stored as JSON string
                
                # Verify complete conversation includes assistant response
                self.assertEqual(len(msgs), 2)  # user message + assistant response
                self.assertEqual(msgs[0], messages[0])  # original user message
                self.assertEqual(msgs[1]["role"], "assistant")  # assistant response
                self.assertIn("content", msgs[1])  # assistant content
                self.assertIn("tool_calls", msgs[1])  # tool calls should be present
                self.assertEqual(len(msgs[1]["tool_calls"]), 1)  # one tool call
                self.assertEqual(msgs[1]["tool_calls"][0]["function"]["name"], "get_weather")
                self.assertEqual(ts, tools)


