# -*- coding: utf-8 -*-
"""Unit tests for Novita tracing."""
import unittest
from unittest.mock import MagicMock
from agentscope.model import NovitaChatModel, OpenAIChatModel
from agentscope.tracing._extractor import _get_provider_name
from agentscope.tracing._attributes import ProviderNameValues

class TestNovitaTracing(unittest.TestCase):
    """Test cases for Novita tracing."""

    def test_get_provider_name_novita_model(self) -> None:
        """Test _get_provider_name with NovitaChatModel."""
        model = MagicMock(spec=NovitaChatModel)
        model.__class__.__name__ = "NovitaChatModel"
        
        provider = _get_provider_name(model)
        self.assertEqual(provider, ProviderNameValues.NOVITA)

    def test_get_provider_name_openai_model_with_novita_url(self) -> None:
        """Test _get_provider_name with OpenAIChatModel and Novita URL."""
        model = MagicMock(spec=OpenAIChatModel)
        model.__class__.__name__ = "OpenAIChatModel"
        model.client = MagicMock()
        model.client.base_url = "https://api.novita.ai/openai"
        
        provider = _get_provider_name(model)
        self.assertEqual(provider, ProviderNameValues.NOVITA)
