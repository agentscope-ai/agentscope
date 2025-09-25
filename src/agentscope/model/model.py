# -*- coding: utf-8 -*-
"""The model wrapper base class."""

from __future__ import annotations
import inspect
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable, Union, List, Optional

from loguru import logger

from ._model_usage import ChatUsage
from .response import ModelResponse
from ..exception import ResponseParsingError

from ..manager import FileManager
from ..manager import MonitorManager
from ..message import Msg
from ..utils.common import _get_timestamp
from ..constants import _DEFAULT_MAX_RETRIES
from ..constants import _DEFAULT_RETRY_INTERVAL


def _response_parse_decorator(
    model_call: Callable,
) -> Callable:
    """A decorator for parsing the response of model call. It will take
    `parse_func`, `fault_handler` and `max_retries` as arguments. The
    detailed process is as follows:

        1. If `parse_func` is provided, then the response will be parsed first.

        2. If the parsing fails (throws an exception), then response generation
        will be repeated for `max_retries` times and parsed again.

        3. After `max_retries` times, if the parsing still fails, then if
        `fault_handler` is provided, the response will be processed by
        `fault_handler`.
    """

    # check if the decorated `model_call` function uses the default
    # arguments of this decorator.
    parameters = inspect.signature(model_call).parameters

    for name in parameters.keys():
        if name in ["parse_func", "max_retries"]:
            logger.warning(
                f"The argument {name} is used by the decorator, "
                f"which will not be passed to the model call "
                f"function.",
            )

    @wraps(model_call)
    def checking_wrapper(self: Any, *args: Any, **kwargs: Any) -> dict:
        # Step1: Extract parse_func and fault_handler
        parse_func = kwargs.pop("parse_func", None)
        fault_handler = kwargs.pop("fault_handler", None)
        max_retries = kwargs.pop("max_retries", None) or _DEFAULT_MAX_RETRIES

        # Step2: Call the model and parse the response
        # Return the response directly if parse_func is not provided
        if parse_func is None:
            return model_call(self, *args, **kwargs)

        # Otherwise, try to parse the response
        for itr in range(1, max_retries + 1):
            # Call the model
            response = model_call(self, *args, **kwargs)

            # Parse the response if needed
            try:
                return parse_func(response)
            except ResponseParsingError as e:
                if itr < max_retries:
                    logger.warning(
                        f"Fail to parse response ({itr}/{max_retries}):\n"
                        f"{response}.\n"
                        f"{e.__class__.__name__}: {e}",
                    )
                    time.sleep(_DEFAULT_RETRY_INTERVAL * itr)
                else:
                    if fault_handler is not None and callable(fault_handler):
                        return fault_handler(response)
                    else:
                        raise
        return {}

    return checking_wrapper


class ModelWrapperBase(ABC):
    """The base class for model wrapper."""

    model_type: str
    """The type of the model wrapper, which is to identify the model wrapper
    class in model configuration."""

    config_name: str
    """The name of the model configuration."""

    model_name: str
    """The name of the model, which is used in model api calling."""

    _class_hooks_save_model_invocation: dict[
        str,
        Callable[
            [
                ModelWrapperBase,  # self object
                str,  # model invocation id
                str,  # timestamp
                dict,  # arguments
                Union[dict, str],  # response
                dict,  # usage
            ],
            None,
        ],
    ] = OrderedDict()
    """The class hooks in saving model invocations, which takes the model
    wrapper object, model invocation id, timestamp, arguments, response,
     and usage as input"""

    def __init__(
        self,  # pylint: disable=W0613
        config_name: Optional[str] = None,
        model_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Base class for model wrapper.

        All model wrappers should inherit this class and implement the
        `__call__` function.

        Args:
            config_name (`Optional[str]`, defaults to `None`):
                The id of the model, which is used to extract configuration
                from the config file.
            model_name (`Optional[str]`, defaults to `None`):
                The name of the model.
        """
        self.monitor = MonitorManager.get_instance()

        self.config_name = config_name

        if model_name is None:
            raise ValueError(
                "Model name should be provided for model "
                f"configuration [{config_name}].",
            )

        self.model_name = model_name

        logger.debug(f"Initialize model by configuration [{config_name}]")

    @abstractmethod
    def __call__(self, *args: Any, **kwargs: Any) -> ModelResponse:
        """Processing input with the model."""
        raise NotImplementedError(
            f"Model Wrapper [{type(self).__name__}]"
            f" is missing the required `__call__`"
            f" method.",
        )

    def format(
        self,
        *args: Union[Msg, list[Msg], None],
        multi_agent_mode: bool = True,
    ) -> Union[List[dict], str]:
        """Format the input messages into the format that the model
        API required."""
        raise NotImplementedError(
            f"The method `format` is not implemented for model wrapper "
            f"[{type(self).__name__}].",
        )

    def format_tools_json_schemas(
        self,
        schemas: dict[str, dict],
    ) -> list[dict]:
        """Format the JSON schemas of the tool functions to the format that
        the model API provider expects.

        Example:
            An example of the input schemas parsed from the service toolkit

            ..code-block:: json

                {
                    "bing_search": {
                        "type": "function",
                        "function": {
                            "name": "bing_search",
                            "description": "Search the web using Bing.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "The search query.",
                                    }
                                },
                                "required": ["query"],
                            }
                        }
                    }
                }

        Args:
            schemas (`dict[str, dict]`):
                The tools JSON schemas parsed from the service toolkit module,
                which can be accessed by `service_toolkit.json_schemas`.

        Returns:
            `list[dict]`:
                The formatted JSON schemas of the tool functions.
        """

        raise NotImplementedError(
            f"The method `format_tools_json_schemas` is not implemented "
            f"for model wrapper [{type(self).__name__}].",
        )

    def _save_model_invocation(
        self,
        arguments: dict,
        response: Union[dict, str],
        usage: Optional[ChatUsage] = None,
    ) -> None:
        """Save model invocation."""
        model_class = self.__class__.__name__
        timestamp = _get_timestamp("%Y%m%d-%H%M%S")

        usage_dict = usage.model_dump() if usage else {}

        invocation_record = {
            "model_class": model_class,
            "timestamp": timestamp,
            "arguments": arguments,
            "response": response,
            "usage": usage_dict,
        }

        invocation_id = f"model_{model_class}_{timestamp}"

        FileManager.get_instance().save_api_invocation(
            invocation_id,
            invocation_record,
        )

        # hooks
        for (
            hook
        ) in ModelWrapperBase._class_hooks_save_model_invocation.values():
            hook(
                self,
                invocation_id,
                timestamp,
                arguments,
                response,
                usage_dict,
            )

    @classmethod
    def register_save_model_invocation_hook(
        cls,
        hook_name: str,
        hook: Callable[
            [
                ModelWrapperBase,  # self object
                str,  # model invocation id
                str,  # timestamp
                dict,  # arguments
                dict,  # response
                dict,  # usage
            ],
            None,
        ],
    ) -> None:
        """Register save model invocation hook.

        Args:
            hook_name (`str`):
                The name of the hook.
            hook (`Callable[[dict, dict], None]`):
                The hook function, which should take
        """
        if hook_name in cls._class_hooks_save_model_invocation:
            logger.warning(
                f"Hook [{hook_name}] already exists. "
                f"Overwriting the existing hook.",
            )

        cls._class_hooks_save_model_invocation[hook_name] = hook

    @classmethod
    def remove_save_model_invocation_hook(
        cls,
        hook_name: str,
    ) -> None:
        """Remove model invocation saving hook by its name.

        Args:
            hook_name (`str`):
                The name of the hook to be removed.
        """
        if hook_name in cls._class_hooks_save_model_invocation:
            cls._class_hooks_save_model_invocation.pop(hook_name)
        else:
            logger.warning(
                f"Hook [{hook_name}] doesn't exist. "
                f"Cannot remove the non-existing hook.",
            )

    @classmethod
    def clear_save_model_invocation_hook(cls) -> None:
        """Clear all the hooks in saving model invocations."""
        cls._class_hooks_save_model_invocation.clear()
