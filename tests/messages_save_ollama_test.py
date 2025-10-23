# -*- coding: utf-8 -*-
"""Tests for messages saving with OllamaChatModel."""

import json
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import ollama  # Ensure ollama module is imported for patching
from agentscope.model import OllamaChatModel, enable_messages_save


class TestMessagesSaveOllama(IsolatedAsyncioTestCase):
    """Verify messages saving using hook mechanism with enhanced features."""

    async def test_non_stream_with_tags(self) -> None:
        """Test non-streaming with custom tags."""
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "out.jsonl")
                
                # Create model
                model = OllamaChatModel(
                    model_name="llama3.2",
                    stream=False,
                )
                model.client = mock_client
                
                # Enable saving with tags
                enable_messages_save(
                    model,
                    save_path=out,
                    tags={"provider": "ollama", "model": "llama"}
                )

                # Mock response
                response = AsyncMock()
                message = Mock()
                message.thinking = None
                message.content = "Response"
                message.tool_calls = []
                
                response.message = message
                response.done = True
                response.prompt_eval_count = 1
                response.eval_count = 1

                mock_client.chat = AsyncMock(return_value=response)

                messages = [{"role": "user", "content": "hi"}]
                await model(messages)

                # Verify file and tags
                self.assertTrue(os.path.exists(out))
                with open(out, "r", encoding="utf-8") as f:
                    lines = [json.loads(l) for l in f.read().strip().splitlines()]
                self.assertEqual(len(lines), 1)
                
                metadata = lines[0].get("metadata", {})
                self.assertEqual(metadata.get("provider"), "ollama")
                self.assertEqual(metadata.get("model"), "llama")
                
                # Verify history
                self.assertEqual(len(model.messages_call_history), 1)

    async def test_memory_only(self) -> None:
        """Test memory-only mode."""
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            
            # Create model
            model = OllamaChatModel(
                model_name="llama3.2",
                stream=False,
            )
            model.client = mock_client
            
            # Enable memory-only
            enable_messages_save(model, tags={"mode": "memory_only"})
            
            # Mock response
            response = AsyncMock()
            message = Mock()
            message.thinking = None
            message.content = "Response"
            message.tool_calls = []
            response.message = message
            response.done = True
            response.prompt_eval_count = 1
            response.eval_count = 1
            mock_client.chat = AsyncMock(return_value=response)
            
            # Make calls
            await model([{"role": "user", "content": "test"}])
            await model([{"role": "user", "content": "test2"}])
            
            # Verify history
            self.assertEqual(len(model.messages_call_history), 2)
            self.assertEqual(
                model.messages_call_history[0]['metadata']['mode'],
                "memory_only"
            )

    async def test_export_history(self) -> None:
        """Test export functionality."""
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            
            with tempfile.TemporaryDirectory() as td:
                model = OllamaChatModel(
                    model_name="llama3.2",
                    stream=False,
                )
                model.client = mock_client
                
                # Memory-only mode
                enable_messages_save(model, tags={"test": "export"})
                
                # Mock response
                response = AsyncMock()
                message = Mock()
                message.thinking = None
                message.content = "Response"
                message.tool_calls = []
                response.message = message
                response.done = True
                response.prompt_eval_count = 1
                response.eval_count = 1
                mock_client.chat = AsyncMock(return_value=response)
                
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
