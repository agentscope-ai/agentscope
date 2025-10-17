# -*- coding: utf-8 -*-
# pylint: disable=missing-function-docstring
# pylint: disable=unused-argument
"""Learn related tests in agentscope."""
from typing import Any, Dict, List
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.model import TrinityChatModel, OpenAIChatModel
from agentscope.learn.workflow import validate_function_signature


async def correct_interface(task: Dict, model: TrinityChatModel) -> float:
    return task["reward"]


async def wrong_interface_1(
    task: Dict,
    model: TrinityChatModel,
    extra: Any,
) -> float:
    return 0.0


async def wrong_interface_2(task: Dict) -> float:
    return 0.0


async def wrong_interface_3(task: List, model: TrinityChatModel) -> float:
    return 0.0


async def wrong_interface_4(task: Dict, model: OpenAIChatModel) -> float:
    return 0.0


async def wrong_interface_5(task: Dict, model: TrinityChatModel) -> str:
    return "0.0"


class AgentLearnTest(IsolatedAsyncioTestCase):
    """Test the learning functionality of agents."""

    async def test_workflow_interface_validate(self) -> None:
        """Test the interface of workflow function."""
        self.assertTrue(
            validate_function_signature(correct_interface),
        )
        self.assertFalse(
            validate_function_signature(wrong_interface_1),
        )
        self.assertFalse(
            validate_function_signature(wrong_interface_2),
        )
        self.assertFalse(
            validate_function_signature(wrong_interface_3),
        )
        self.assertFalse(
            validate_function_signature(wrong_interface_4),
        )
        self.assertFalse(
            validate_function_signature(wrong_interface_5),
        )
