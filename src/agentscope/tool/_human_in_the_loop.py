# -*- coding: utf-8 -*-
""""""
from typing import Any, Coroutine

import shortuuid

from agentscope.message import Msg
from . import ToolResponse
from ..message import ToolResultBlock, TextBlock, Msg, ToolUseBlock

from ..module import StateModule


class HumanInTheLoop(StateModule):
    """The human-in-the-loop tool class in AgentScope, responsible for
    recording tool calls that require user confirmation before execution,
    the external tool calls, and handling the user confirmation result and
    the external tool execution result.
    """

    async def ask_for_confirmation(self, tool_calls: list[ToolResultBlock]):
        """Ask for user confirmation for the specified tool calls.

        """

    async def record_confirmation_requests(self, tool_calls: list[ToolUseBlock]) -> \
    tuple[Msg, Msg]:
        tool_names = ", ".join(
            [_.get("name") for _ in tool_calls if _.get("name")]
        )

        return Msg(
            "system",
            [
                ToolResultBlock(
                    type="tool_result",
                    id=_.get("id"),
                    name=_.get("name"),
                    # TODO: 我想在这里向agent说明，我们需要用户确认才能执行这个工具调用，稍后会有一个ask_for_confirmation的工具被自动唤起并等待用户的确认结果
                    output=(
                        f"<system-info>The tool {_.get('name')} requires user "
                        f"confirmation before execution. The "
                        f"`ask_for_confirmation` function will be called "
                        f"automatically to wait for user confirmation."
                        f"</system-info>"
                    )
                ) for _ in tool_calls
            ],
            "system"
        ), Msg(
            "assistant",
            [
                ToolUseBlock(
                    type="tool_use",
                    id=shortuuid.uuid(),
                    name="ask_for_confirmation",
                    input={
                        "tool_name": _.get("name"),
                        "input": _.get("input"),
                    }
                ) for _ in tool_calls
            ],
            "assistant"
        )

    async def handle_confirmation_result(self, tool_result: ToolResultBlock):
        """Handle the user confirmation result for the specified tool call."""
        # Filter the approved tool calls from the tool_result of the
        # `ask_for_confirmation` tool
        # 你的目标是什么？
        # 用户好用！
        # 策略是什么
        ToolResultBlock(
            type="tool_result",
            id=tool_result.id,
            name=tool_result.name,
            output=tool_result.output,
        )


        return Msg(
            "system",
            TextBlock()
        )

