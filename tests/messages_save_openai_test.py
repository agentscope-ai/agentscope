# -*- coding: utf-8 -*-
"""Tests for messages saving functionality with OpenAIChatModel."""

import json
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import openai  # Ensure openai module is imported for patching
from agentscope.model import OpenAIChatModel, enable_messages_save


class TestMessagesSaveOpenAI(IsolatedAsyncioTestCase):
    """Verify messages saving functionality works with enhanced features."""

    async def test_non_stream_with_tags(self) -> None:
        """Test non-streaming with custom tags."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "out.jsonl")
                
                # Create model
                model = OpenAIChatModel(
                    model_name="gpt-4",
                    api_key="k",
                    stream=False,
                )
                model.client = mock_client
                
                # Enable saving with tags
                enable_messages_save(
                    model,
                    save_path=out,
                    tags={"provider": "openai", "test": "unit"}
                )

                # Mock response
                response = AsyncMock()
                response.choices = [Mock()]
                
                message = Mock()
                message.content = "Response"
                message.reasoning_content = None
                message.audio = None
                message.parsed = None
                message.tool_calls = []
                
                response.choices[0].message = message
                response.usage = AsyncMock()
                response.usage.prompt_tokens = 1
                response.usage.completion_tokens = 1
                
                mock_client.chat.completions.create = AsyncMock(return_value=response)

                messages = [{"role": "user", "content": "hi"}]
                await model(messages)

                # Verify file and tags
                self.assertTrue(os.path.exists(out))
                with open(out, "r", encoding="utf-8") as f:
                    lines = [json.loads(l) for l in f.read().strip().splitlines()]
                self.assertEqual(len(lines), 1)
                
                metadata = lines[0].get("metadata", {})
                self.assertEqual(metadata.get("provider"), "openai")
                self.assertEqual(metadata.get("test"), "unit")
                
                # Verify history
                self.assertEqual(len(model.messages_call_history), 1)

    async def test_memory_only(self) -> None:
        """Test memory-only mode."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            
            # Create model
            model = OpenAIChatModel(
                model_name="gpt-4",
                api_key="k",
                stream=False,
            )
            model.client = mock_client
            
            # Enable memory-only mode
            enable_messages_save(model, tags={"mode": "memory"})
            
            # Mock response
            response = AsyncMock()
            response.choices = [Mock()]
            message = Mock()
            message.content = "Response"
            message.reasoning_content = None
            message.audio = None
            message.parsed = None
            message.tool_calls = []
            response.choices[0].message = message
            response.usage = AsyncMock()
            response.usage.prompt_tokens = 1
            response.usage.completion_tokens = 1
            mock_client.chat.completions.create = AsyncMock(return_value=response)
            
            # Make calls
            await model([{"role": "user", "content": "test"}])
            await model([{"role": "user", "content": "test2"}])
            
            # Verify history
            self.assertEqual(len(model.messages_call_history), 2)

    async def test_export_history(self) -> None:
        """Test export history functionality."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            
            with tempfile.TemporaryDirectory() as td:
                model = OpenAIChatModel(
                    model_name="gpt-4",
                    api_key="k",
                    stream=False,
                )
                model.client = mock_client
                
                # Memory-only mode
                enable_messages_save(model, tags={"export": "test"})
                
                # Mock response
                response = AsyncMock()
                response.choices = [Mock()]
                message = Mock()
                message.content = "Response"
                message.reasoning_content = None
                message.audio = None
                message.parsed = None
                message.tool_calls = []
                response.choices[0].message = message
                response.usage = AsyncMock()
                response.usage.prompt_tokens = 1
                response.usage.completion_tokens = 1
                mock_client.chat.completions.create = AsyncMock(return_value=response)
                
                # Make calls
                await model([{"role": "user", "content": "test"}])
                await model([{"role": "user", "content": "test2"}])
                
                # Export
                export_path = os.path.join(td, "exported.jsonl")
                count = model.export_messages_call_history(export_path)
                
                # Verify
                self.assertEqual(count, 2)
                self.assertTrue(os.path.exists(export_path))
                with open(export_path, "r") as f:
                    lines = f.readlines()
                self.assertEqual(len(lines), 2)
