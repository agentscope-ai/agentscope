# -*- coding: utf-8 -*-
"""The user input related classes."""
import json.decoder
import time
from abc import abstractmethod
from dataclasses import dataclass
from queue import Queue
from threading import Event
from typing import Any, Type, List

import jsonschema
import requests
import shortuuid
import socketio
from pydantic import BaseModel
import json5

from .. import _config
from .._logging import logger
from ..message import (
    TextBlock,
    VideoBlock,
    AudioBlock,
    ImageBlock,
)


@dataclass
class UserInputData:
    """The user input data."""

    blocks_input: List[TextBlock | ImageBlock | AudioBlock | VideoBlock] = None
    """The text input from the user"""

    structured_input: dict[str, Any] | None = None
    """The structured input from the user"""


class UserInputBase:
    """The base class used to handle the user input from different sources."""

    @abstractmethod
    async def __call__(
        self,
        agent_id: str,
        agent_name: str,
        *args: Any,
        structured_model: Type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> UserInputData:
        """The user input method, which returns the user input and the
        required structured data.

        Args:
            agent_id (`str`):
                The agent identifier.
            agent_name (`str`):
                The agent name.
            structured_model (`Type[BaseModel] | None`, optional):
                A base model class that defines the structured input format.

        Returns:
            `UserInputData`:
                The user input data.
        """


class TerminalUserInput(UserInputBase):
    """The terminal user input."""

    def __init__(self, input_hint: str = "User Input: ") -> None:
        """Initialize the terminal user input with a hint."""
        self.input_hint = input_hint

    async def __call__(
        self,
        agent_id: str,
        agent_name: str,
        *args: Any,
        structured_model: Type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> UserInputData:
        """Handle the user input from the terminal.

        Args:
            agent_id (`str`):
                The agent identifier.
            agent_name (`str`):
                The agent name.
            structured_model (`Type[BaseModel] | None`, optional):
                A base model class that defines the structured input format.

        Returns:
            `UserInputData`:
                The user input data.
        """

        text_input = input(self.input_hint)

        structured_input = None
        if structured_model is not None:
            structured_input = {}

            json_schema = structured_model.model_json_schema()
            required = json_schema.get("required", [])
            print("Structured input (press Enter to skip for optional):)")

            for key, item in json_schema.get("properties").items():
                requirements = {**item}
                requirements.pop("title")

                while True:
                    res = input(f"\t{key} ({requirements}): ")

                    if res == "":
                        if key in required:
                            print(f"Key {key} is required.")
                            continue

                        res = item.get("default", None)

                    if item.get("type").lower() == "integer":
                        try:
                            res = json5.loads(res)
                        except json.decoder.JSONDecodeError as e:
                            print(
                                "\033[31mInvalid input with error:\n"
                                "```\n"
                                f"{e}\n"
                                "```\033[0m",
                            )
                            continue

                    try:
                        jsonschema.validate(res, item)
                        structured_input[key] = res
                        break
                    except jsonschema.ValidationError as e:
                        print(
                            f"\033[31mValidation error:\n```\n{e}\n```\033[0m",
                        )
                        time.sleep(0.5)

        return UserInputData(
            blocks_input=[TextBlock(type="text", text=text_input)],
            structured_input=structured_input,
        )


class StudioUserInput(UserInputBase):
    """The class that host the user input on the AgentScope Studio."""

    _websocket_namespace: str = "/python"

    def __init__(
        self,
        studio_url: str,
        run_id: str,
        max_retries: int = 3,
        reconnect_attempts: int = 3,
        reconnection_delay: int = 1,
        reconnection_delay_max: int = 5,
    ) -> None:
        """Initialize the StudioUserInput object.

        Args:
            studio_url (`str`):
                The URL of the AgentScope Studio.
            run_id (`str`):
                The current run identity.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries to get user input.
        """
        self._is_connected = False
        self._is_reconnecting = False

        self.studio_url = studio_url
        self.run_id = run_id
        self.max_retries = max_retries

        # Init Websocket
        self.sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=reconnect_attempts,
            reconnection_delay=reconnection_delay,
            reconnection_delay_max=reconnection_delay_max,
        )
        self.input_queues = {}
        self.input_events = {}

        @self.sio.on("connect", namespace=self._websocket_namespace)
        def on_connect() -> None:
            self._is_connected = True
            logger.info(
                'Connected to AgentScope Studio at "%s" with '
                'run name "%s".',
                self.studio_url,
                run_id,
            )
            logger.info(
                "View the run at: %s/dashboard/projects/%s",
                self.studio_url,
                _config.project,
            )

        @self.sio.on("disconnect", namespace=self._websocket_namespace)
        def on_disconnect() -> None:
            self._is_connected = False
            logger.info(
                "Disconnected from AgentScope Studio at %s",
                self.studio_url,
            )

        @self.sio.on("reconnect", namespace=self._websocket_namespace)
        def on_reconnect(attempt_number: int) -> None:
            self._is_connected = True
            self._is_reconnecting = False
            logger.info(
                "Reconnected to AgentScope Studio at %s with run_id %s after "
                "%d attempts",
                self.studio_url,
                self.run_id,
                attempt_number,
            )

        @self.sio.on("reconnect_attempt", namespace=self._websocket_namespace)
        def on_reconnect_attempt(attempt_number: int) -> None:
            self._is_reconnecting = True
            logger.info(
                "Attempting to reconnect to AgentScope Studio at %s "
                "(attempt %d)",
                self.studio_url,
                attempt_number,
            )

        @self.sio.on("reconnect_failed", namespace=self._websocket_namespace)
        def on_reconnect_failed() -> None:
            self._is_reconnecting = False
            logger.error(
                "Failed to reconnect to AgentScope Studio at %s",
                self.studio_url,
            )

        @self.sio.on("reconnect_error", namespace=self._websocket_namespace)
        def on_reconnect_error(error: Any) -> None:
            logger.error(
                "Error while reconnecting to AgentScope Studio at %s: %s",
                self.studio_url,
                str(error),
            )

        # The AgentScope Studio backend send the "sendUserInput" event to
        # the current python run
        @self.sio.on("forwardUserInput", namespace=self._websocket_namespace)
        def receive_user_input(
            request_id: str,
            blocks_input: List[
                TextBlock | ImageBlock | AudioBlock | VideoBlock
            ],
            structured_input: dict[str, Any],
        ) -> None:
            if request_id in self.input_queues:
                self.input_queues[request_id].put(
                    UserInputData(
                        blocks_input=blocks_input,
                        structured_input=structured_input,
                    ),
                )
                self.input_events[request_id].set()

        try:
            self.sio.connect(
                f"{self.studio_url}",
                namespaces=["/python"],
                auth={"run_id": self.run_id},
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to connect to AgentScope Studio at {self.studio_url}",
            ) from e

    def _ensure_connected(
        self,
        timeout: float = 30.0,
        check_interval: float = 5.0,
    ) -> None:
        """Ensure the connection is established or wait for reconnection.

        Args:
            timeout (`float`):
                Maximum time to wait for reconnection in seconds. Defaults
                to 30.0.
            check_interval (`float`):
                Interval between connection checks in seconds. Defaults to 1.0.

        Raises:
            `RuntimeError`:
                If connection cannot be established within timeout.
        """
        if self._is_connected:
            return

        if self._is_reconnecting:
            start_time = time.time()
            while self._is_reconnecting:
                # Check timeout
                elapsed_time = time.time() - start_time
                if elapsed_time > timeout:
                    raise RuntimeError(
                        f"Reconnection timeout after {elapsed_time} seconds",
                    )

                # Log status
                logger.info(
                    "Waiting for reconnection... (%.1fs / %.1fs)",
                    elapsed_time,
                    timeout,
                )

                # Wait for next check
                time.sleep(check_interval)

            # After reconnection attempt completed, check final status
            if self._is_connected:
                return

        # Not connected and not reconnecting
        raise RuntimeError(
            f"Not connected to AgentScope Studio at {self.studio_url}.",
        )

    async def __call__(  # type: ignore[override]
        self,
        agent_id: str,
        agent_name: str,
        *args: Any,
        structured_model: Type[BaseModel] | None = None,
    ) -> UserInputData:
        """Get the user input from AgentScope Studio.

        Args:
            agent_id (`str`):
                The identity of the agent.
            agent_name (`str`):
                The name of the agent.
            structured_model (`Type[BaseModel] | None`, optional):
                The base model class of the structured input.

        Raises:
            `RuntimeError`:
                Failed to get user input from AgentScope Studio.

        Returns:
            `UserInputData`:
                The user input.
        """
        self._ensure_connected()

        request_id = shortuuid.uuid()

        self.input_queues[request_id] = Queue()
        self.input_events[request_id] = Event()

        if structured_model is None:
            structured_input = None
        else:
            structured_input = structured_model.model_json_schema()

        n_retry = 0
        while True:
            try:
                response = requests.post(
                    f"{self.studio_url}/trpc/requestUserInput",
                    json={
                        "requestId": request_id,
                        "runId": self.run_id,
                        "agentId": agent_id,
                        "agentName": agent_name,
                        "structuredInput": structured_input,
                    },
                )
                response.raise_for_status()
                break
            except Exception as e:
                if n_retry < self.max_retries:
                    n_retry += 1
                    continue

                raise RuntimeError(
                    "Failed to get user input from AgentScope Studio",
                ) from e

        try:
            self.input_events[request_id].wait()
            response_data = self.input_queues[request_id].get()
            return response_data

        finally:
            self.input_queues.pop(request_id, None)
            self.input_events.pop(request_id, None)

    def __del__(self) -> None:
        """Cleanup socket connection when object it destroyed"""
        try:
            self.sio.disconnect()
        except Exception as e:
            logger.error(
                "Failed to disconnect from AgentScope Studio at %s: %s",
                self.studio_url,
                str(e),
            )
