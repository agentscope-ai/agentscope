# -*- coding: utf-8 -*-
"""The Chinese tools for ACEBench evaluation."""
from functools import wraps
from typing import Callable, Any

from ._ace_tools_api import (
    ReminderApi,
    FoodPlatformApi,
    TravelApi,
    MessageApi,
)
from ...message import TextBlock
from ...tool import ToolResponse


def _tool_function_wrapper(get_tool_function: Callable) -> Callable:
    """Wrap the tool function result to be ToolResponse."""

    @wraps(get_tool_function)
    def wrapper(self: "ACEPhone", name: str) -> Callable:
        """Wrap the tool function to return ToolResponse."""
        tool_function = get_tool_function(self, name)

        @wraps(tool_function)
        def wrapper_tool_function(*args: Any, **kwargs: Any) -> ToolResponse:
            """The wrapped tool function"""
            res = tool_function(*args, **kwargs)
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=str(res),
                    ),
                ],
            )

        return wrapper_tool_function

    return wrapper


class ACEPhone:
    """Simulate a user phone with various apps and functionalities in
    ACEBench. The code is implemented with reference to the
    `ACEBench <https://github.com/ACEBench/ACEBench>`_.
    """

    def __init__(self) -> None:
        """Initialize the shared state and apps for the ACEPhone."""
        self._state = {
            "wifi": False,
            "logged_in": False,
        }
        self._message_app = MessageApi(self._state)
        self._reminder_app = ReminderApi(self._state)
        self._food_platform_app = FoodPlatformApi(self._state)
        self._travel = TravelApi()

    def turn_on_wifi(self) -> dict[str, bool | str]:
        """开启WiFi连接。"""
        self._state["wifi"] = True
        return {"status": True, "message": "wifi已经打开"}

    def login_device(self) -> dict[str, bool | str]:
        """登录设备。"""
        self._state["logged_in"] = True
        return {"status": True, "message": "设备已经登录"}

    def load_initial_config(self, initial_config: dict) -> None:
        """Load the initial config from the application configuration."""
        # Empty initial config
        if len(initial_config) == 0:
            return

        # Fix the typo in ACEBench by renaming "Baspi" to "BaseApi"
        if "Baspi" in initial_config:
            initial_config["BaseApi"] = initial_config.pop("Baspi")

        # Verify state
        assert (
            "BaseApi" in initial_config
            and "wifi" in initial_config["BaseApi"]
            and "logged_in" in initial_config["BaseApi"]
        ), f"Invalid initial config: {initial_config}"

        self._state["wifi"] = initial_config["BaseApi"]["wifi"]
        self._state["logged_in"] = initial_config["BaseApi"]["logged_in"]

    def get_current_state(self) -> list[dict]:
        """Follow ACEBench to get the current state of the ACEPhone."""
        return [
            {"BaseApi": self._state},
            self._message_app.get_state_dict(),
            self._reminder_app.get_state_dict(),
            self._food_platform_app.get_state_dict(),
            self._travel.get_state_dict(),
        ]

    @_tool_function_wrapper
    def get_tool_function(self, name: str) -> Callable:
        """Get a tool function by name."""
        if name in [
            "turn_on_wifi",
            "login_device",
        ]:
            return getattr(self, name)

        if name in self._message_app.tool_functions:
            return getattr(self._message_app, name)

        if name in self._food_platform_app.tool_functions:
            return getattr(self._food_platform_app, name)

        if name in self._reminder_app.tool_functions:
            return getattr(self._reminder_app, name)

        if name in self._travel.tool_functions:
            return getattr(self._travel, name)

        raise ValueError(
            f"Tool function '{name}' not found in ACEPhone.",
        )
