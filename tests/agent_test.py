# -*- coding: utf-8 -*-
"""The agent test case."""
import json
from typing import Any, Literal
from unittest.async_case import IsolatedAsyncioTestCase

from pydantic import BaseModel

from agentscope.agent import Agent, CompressionConfig
from agentscope.formatter import FormatterBase
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel, ChatModelBase


class CustomSummarySchema(BaseModel):
    """Custom summary schema for testing."""

    summary: str


class CustomModel(ChatModelBase):
    """Custom model for testing."""

    type: Literal["custom_model"] = "custom_model"
    custom_field: str = "custom_value"

    async def _call_api(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> dict:
        """Mock API call method."""
        return {
            "role": "assistant",
            "content": "Custom model response",
        }


class CustomFormatter(FormatterBase):
    """Custom formatter for testing."""

    type: Literal["custom_formatter"] = "custom_formatter"
    custom_field: str = "custom_formatter_value"
    supported_input_media_types: list[str] = ["image/*", "audio/*"]

    async def format(self, *args: Msg) -> list[dict]:
        """Mock format method."""
        return [{"role": "user", "content": "formatted"}]


class AgentTest(IsolatedAsyncioTestCase):
    """The agent test case."""

    async def test_serialization(self) -> None:
        """The test for agent serialization."""
        agent = Agent(
            name="test_agent",
            system_prompt="You are a helpful assistant.",
            model=DashScopeChatModel(
                model_name="dashscope-chat-001",
                api_key="test_api_key",
                stream=False,
            ),
            max_retries=5,
            fallback_model=DashScopeChatModel(
                model_name="dashscope-chat-001",
                api_key="test_api_key",
                stream=False,
            ),
            compression=CompressionConfig(
                trigger_threshold=12500,
                keep_recent=10,
                compression_prompt="Test prompt for compression.",
                summary_schema=CustomSummarySchema.model_json_schema(),
                compression_model=DashScopeChatModel(
                    model_name="dashscope-chat-001",
                    api_key="test_api_key",
                    stream=False,
                ),
            ),
            # Initialize from dict to test the serialization and
            # deserialization of the reasoning and acting configs
            reasoning={
                "max_iters": 50,
            },
            acting={
                "parallel": False,
            },
        )

        # Serialize to JSON
        json_str = agent.model_dump_json()
        agent_dict = json.loads(json_str)

        # Deserialize from JSON
        agent_restored = Agent.model_validate(agent_dict)

        # Verify restored agent matches original by comparing serialized forms
        self.assertDictEqual(
            json.loads(agent.model_dump_json()),
            json.loads(agent_restored.model_dump_json()),
        )

    async def test_custom_model_and_formatter(self) -> None:
        """The test for custom model and formatter."""
        agent = Agent(
            name="custom_agent",
            system_prompt="You are a custom assistant.",
            model=CustomModel(
                model_name="custom-model-001",
                custom_field="my_custom_value",
                stream=False,
                max_retries=3,
                fallback_model_name=None,
                formatter=CustomFormatter(
                    custom_field="my_custom_formatter_value",
                ),
            ),
        )

        # Serialize to JSON
        json_str = agent.model_dump_json()
        agent_dict = json.loads(json_str)

        # Verify serialized data contains custom types
        self.assertEqual(agent_dict["model"]["type"], "custom_model")
        self.assertEqual(
            agent_dict["model"]["formatter"]["type"],
            "custom_formatter",
        )

        # Deserialize with custom classes provided via context
        agent_restored = Agent.model_validate(
            agent_dict,
            context={
                "custom_model_classes": [CustomModel],
                "custom_formatter_classes": [CustomFormatter],
            },
        )

        # Verify restored agent matches original by comparing serialized forms
        self.assertDictEqual(
            json.loads(agent.model_dump_json()),
            json.loads(agent_restored.model_dump_json()),
        )

        # Verify that deserialization without custom classes raises an error
        with self.assertRaises(ValueError):
            Agent.model_validate(agent_dict)
