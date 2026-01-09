# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""Learn related tests in agentscope."""
from typing import Any, Dict, List
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.model import TrinityChatModel, OpenAIChatModel
from agentscope.tune._workflow import _validate_function_signature


async def correct_interface(task: Dict, model: TrinityChatModel) -> float:
    """Correct interface matching the workflow type."""
    return task["reward"]


async def wrong_interface_1(
    task: Dict,
    model: TrinityChatModel,
    extra: Any,
) -> float:
    """Wrong interface with extra argument."""
    return 0.0


async def wrong_interface_2(task: Dict) -> float:
    """Wrong interface with missing argument."""
    return 0.0


async def wrong_interface_3(task: List, model: TrinityChatModel) -> float:
    """Wrong interface with wrong task type."""
    return 0.0


async def wrong_interface_4(task: Dict, model: OpenAIChatModel) -> float:
    """Wrong interface with wrong model type."""
    return 0.0


async def wrong_interface_5(task: Dict, model: TrinityChatModel) -> str:
    """Wrong interface with wrong return type."""
    return "0.0"


class AgentLearnTest(IsolatedAsyncioTestCase):
    """Test the learning functionality of agents."""

    async def test_workflow_interface_validate(self) -> None:
        """Test the interface of workflow function."""
        self.assertTrue(
            _validate_function_signature(correct_interface),
        )
        self.assertFalse(
            _validate_function_signature(wrong_interface_1),
        )
        self.assertFalse(
            _validate_function_signature(wrong_interface_2),
        )
        self.assertFalse(
            _validate_function_signature(wrong_interface_3),
        )
        self.assertFalse(
            _validate_function_signature(wrong_interface_4),
        )
        self.assertFalse(
            _validate_function_signature(wrong_interface_5),
        )
