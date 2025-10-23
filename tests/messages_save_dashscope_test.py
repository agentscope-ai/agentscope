# -*- coding: utf-8 -*-
"""Tests for messages saving with DashScopeChatModel."""

import json
import os
import tempfile
from http import HTTPStatus
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import dashscope  # Ensure dashscope module is imported for patching
from agentscope.model import DashScopeChatModel, enable_messages_save


class TestMessagesSaveDashScope(IsolatedAsyncioTestCase):
    """Verify messages saving using the new hook mechanism with enhanced features."""

    async def test_non_stream_collection_with_tags(self) -> None:
        """Test non-streaming with custom tags."""
        with patch("dashscope.aigc.generation.AioGeneration.call") as mock_call:
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "out.jsonl")
                
                # Create model without save parameters
                model = DashScopeChatModel(
                    model_name="qwen-turbo",
                    api_key="k",
                    stream=False,
                )
                
                # Enable messages saving using hook with custom tags
                enable_messages_save(
                    model, 
                    save_path=out,
                    tags={"experiment": "test1", "version": "v1"}
                )

                # Build a DashScope-like response object
                response = Mock()
                response.status_code = HTTPStatus.OK
                response.output = Mock()
                response.output.choices = [Mock()]
                response.output.choices[0].message = {
                    "content": "I'll check the weather for you.",
                    "reasoning_content": "Let me think about this...",
                    "tool_calls": [],
                }
                response.usage = Mock()
                response.usage.input_tokens = 5
                response.usage.output_tokens = 10
                mock_call.return_value = response

                messages = [{"role": "user", "content": "What's the weather?"}]
                await model(messages)

                # Verify file was written
                self.assertTrue(os.path.exists(out))
                with open(out, "r", encoding="utf-8") as f:
                    lines = [json.loads(l) for l in f.read().strip().splitlines()]
                self.assertEqual(len(lines), 1)
                
                rec = lines[0]
                metadata = rec.get("metadata", {})
                
                # Verify custom tags
                self.assertEqual(metadata.get("experiment"), "test1")
                self.assertEqual(metadata.get("version"), "v1")
                
                # Verify in-memory history
                self.assertEqual(len(model.messages_call_history), 1)
                hist_meta = model.messages_call_history[0]['metadata']
                self.assertEqual(hist_meta.get("experiment"), "test1")

    async def test_memory_only_mode(self) -> None:
        """Test memory-only mode without file save."""
        with patch("dashscope.aigc.generation.AioGeneration.call") as mock_call:
            # Create model
            model = DashScopeChatModel(
                model_name="qwen-turbo",
                api_key="k",
                stream=False,
            )
            
            # Enable memory-only mode (no save_path)
            enable_messages_save(model, tags={"mode": "memory"})
            
            # Mock response
            response = Mock()
            response.status_code = HTTPStatus.OK
            response.output = Mock()
            response.output.choices = [Mock()]
            response.output.choices[0].message = {
                "content": "Response",
                "reasoning_content": None,
                "tool_calls": [],
            }
            response.usage = Mock()
            response.usage.input_tokens = 5
            response.usage.output_tokens = 10
            mock_call.return_value = response
            
            # Call model twice
            messages = [{"role": "user", "content": "Test"}]
            await model(messages)
            await model(messages)
            
            # Verify history
            self.assertEqual(len(model.messages_call_history), 2)
            self.assertEqual(model.messages_call_history[0]['metadata']['mode'], "memory")

    async def test_export_history(self) -> None:
        """Test exporting history to file."""
        with patch("dashscope.aigc.generation.AioGeneration.call") as mock_call:
            with tempfile.TemporaryDirectory() as td:
                # Create model with memory-only mode
                model = DashScopeChatModel(
                    model_name="qwen-turbo",
                    api_key="k",
                    stream=False,
                )
                
                enable_messages_save(model, tags={"test": "export"})
                
                # Mock response
                response = Mock()
                response.status_code = HTTPStatus.OK
                response.output = Mock()
                response.output.choices = [Mock()]
                response.output.choices[0].message = {
                    "content": "Response",
                    "reasoning_content": None,
                    "tool_calls": [],
                }
                response.usage = Mock()
                response.usage.input_tokens = 5
                response.usage.output_tokens = 10
                mock_call.return_value = response
                
                # Make some calls
                messages = [{"role": "user", "content": "Test"}]
                await model(messages)
                await model(messages)
                
                # Export history
                export_path = os.path.join(td, "exported.jsonl")
                count = model.export_messages_call_history(export_path)
                
                # Verify export
                self.assertEqual(count, 2)
                self.assertTrue(os.path.exists(export_path))
                
                with open(export_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                self.assertEqual(len(lines), 2)

    async def test_streaming_with_tags(self) -> None:
        """Test streaming response with tags."""
        with patch("dashscope.aigc.generation.AioGeneration.call") as mock_call:
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "out.jsonl")
                
                # Create model
                model = DashScopeChatModel(
                    model_name="qwen-turbo",
                    api_key="k",
                    stream=True,
                    enable_thinking=True,
                )
                
                enable_messages_save(model, save_path=out, tags={"stream": "yes"})
                
                # Create mock streaming chunks
                chunks = [
                    self._create_mock_chunk(
                        content="",
                        reasoning_content="Thinking...",
                    ),
                    self._create_mock_chunk(
                        content="Response",
                        reasoning_content="",
                    ),
                ]
                
                mock_call.return_value = self._create_async_generator(chunks)
                
                # Consume stream
                messages = [{"role": "user", "content": "Test"}]
                result = await model(messages)
                responses = []
                async for response in result:
                    responses.append(response)
                
                # Verify
                self.assertTrue(os.path.exists(out))
                with open(out, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                self.assertEqual(len(lines), 1)
                
                rec = json.loads(lines[0])
                metadata = rec.get("metadata", {})
                self.assertEqual(metadata.get("stream"), "yes")

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
        chunk.output.choices[0].message = _Msg(content, reasoning_content, tool_calls)
        
        chunk.usage = Mock()
        chunk.usage.input_tokens = 5
        chunk.usage.output_tokens = 10
        return chunk

    async def _create_async_generator(self, items: list):
        """Create an asynchronous generator."""
        for item in items:
            yield item
