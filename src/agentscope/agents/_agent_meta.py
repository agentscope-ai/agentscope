# -*- coding: utf-8 -*-
"""The metaclass for agents in agentscope."""
import inspect
from copy import deepcopy
from functools import wraps
from typing import (
    Any,
    Dict,
    TYPE_CHECKING,
    Callable,
)

from .._utils._common import _execute_async_or_sync_func

if TYPE_CHECKING:
    from ._agent_base import AgentBase
else:
    AgentBase = "AgentBase"


def _normalize_to_kwargs(
    func: Callable,
    self: Any,
    *args: Any,
    **kwargs: Any,
) -> dict:
    """Normalize the provided positional and keyword arguments into a
    keyword arguments dictionary that matches the function signature."""
    sig = inspect.signature(func)
    try:
        # Bind the provided arguments to the function signature
        bound = sig.bind(self, *args, **kwargs)
        # Apply the default values for parameters
        bound.apply_defaults()

        # Return the arguments in a dictionary format
        res = dict(bound.arguments)
        res.pop("self")
        return res

    except TypeError as e:
        # If failed to bind, we raise a TypeError with more context
        param_names = list(sig.parameters.keys())
        provided_args = len(args)
        provided_kwargs = list(kwargs.keys())

        raise TypeError(
            f"Failed to bind parameters for function '{func.__name__}': {e}\n"
            f"Expected parameters: {param_names}\n"
            f"Provided {provided_args} positional args and kwargs: "
            f"{provided_kwargs}",
        ) from e


def _wrap_with_hooks(
    original_func: Callable,
) -> Callable:
    """A decorator to wrap the original async function with pre- and post-hooks

    Args:
        original_func (`Callable`):
            The original async function to be wrapped with hooks.
    """
    func_name = original_func.__name__.replace("_", "")

    @wraps(original_func)
    async def async_wrapper(
        self: AgentBase,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """The wrapped function, which call the pre- and post-hooks before and
        after the original function."""

        # Unify all positional and keyword arguments into a keyword arguments
        normalized_kwargs = _normalize_to_kwargs(
            original_func,
            self,
            *args,
            **kwargs,
        )

        current_normalized_kwargs = normalized_kwargs
        assert (
            hasattr(self, f"_instance_pre_{func_name}_hooks")
            and hasattr(self, f"_instance_post_{func_name}_hooks")
            and hasattr(self.__class__, f"_class_pre_{func_name}_hooks")
            and hasattr(self.__class__, f"_class_post_{func_name}_hooks")
        ), f"Hooks for {func_name} not found in {self.__class__.__name__}"

        # pre-hooks
        pre_hooks = list(
            getattr(self, f"_instance_pre_{func_name}_hooks").values(),
        ) + list(
            getattr(self, f"_class_pre_{func_name}_hooks").values(),
        )
        for pre_hook in pre_hooks:
            modified_keywords = await _execute_async_or_sync_func(
                pre_hook,
                self,
                deepcopy(current_normalized_kwargs),
            )
            if modified_keywords is not None:
                assert isinstance(modified_keywords, dict), (
                    f"Pre-hook must return a dict of keyword arguments, rather"
                    f" than {type(modified_keywords)} from hook "
                    f"{pre_hook.__name__}"
                )
                current_normalized_kwargs = modified_keywords

        # original function
        # handle positional and keyword arguments specifically
        args = current_normalized_kwargs.get("args", [])
        kwargs = current_normalized_kwargs.get("kwargs", {})
        others = {
            k: v
            for k, v in current_normalized_kwargs.items()
            if k not in ["args", "kwargs"]
        }
        current_output = await original_func(
            self,
            *args,
            **others,
            **kwargs,
        )

        # post_hooks
        post_hooks = list(
            getattr(self, f"_instance_post_{func_name}_hooks").values(),
        ) + list(
            getattr(self, f"_class_post_{func_name}_hooks").values(),
        )
        for post_hook in post_hooks:
            modified_output = await _execute_async_or_sync_func(
                post_hook,
                self,
                deepcopy(current_normalized_kwargs),
                deepcopy(current_output),
            )
            if modified_output is not None:
                current_output = modified_output
        return current_output

    return async_wrapper


class _AgentMeta(type):
    """The agent metaclass that wraps the agent's reply, observe and print
    functions with pre- and post-hooks."""

    def __new__(mcs, name: Any, bases: Any, attrs: Dict) -> Any:
        """Wrap the agent's functions with hooks."""

        for func_name in [
            "reply",
            "print",
            "observe",
        ]:
            if func_name in attrs:
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])

        return super().__new__(mcs, name, bases, attrs)


class _ReActAgentMeta(_AgentMeta):
    """The ReAct metaclass that adds pre- and post-hooks for the _reasoning
    and _acting functions."""

    def __new__(mcs, name: Any, bases: Any, attrs: Dict) -> Any:
        """Wrap the ReAct agent's _reasoning and _acting functions with
        hooks."""

        for func_name in [
            "_reasoning",
            "_acting",
        ]:
            if func_name in attrs:
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])

        return super().__new__(mcs, name, bases, attrs)
