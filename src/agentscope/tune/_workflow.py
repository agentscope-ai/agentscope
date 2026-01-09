# -*- coding: utf-8 -*-
"""Workflow for agent learning."""

from typing import (
    Dict,
    Callable,
    Awaitable,
    get_type_hints,
)

import inspect

from .._logging import logger
from ..model import TrinityChatModel


WorkflowType = Callable[[Dict, TrinityChatModel], Awaitable[float]]


def _validate_function_signature(func: Callable) -> bool:
    """Validate if a function matches the workflow type signature.

    Args:
        func (Callable): The function to validate.
    """
    # check if the function is asynchronous
    if not inspect.iscoroutinefunction(func):
        logger.warning("The function is not asynchronous.")
        return False
    # Define expected parameter types and return type manually
    expected_params = [
        ("task", Dict),
        ("model", TrinityChatModel),
    ]
    expected_return = float

    func_signature = inspect.signature(func)
    func_hints = get_type_hints(func)

    # Check if the number of parameters matches
    if len(func_signature.parameters) != len(expected_params):
        logger.warning(
            "Expected %d parameters, but got %d",
            len(expected_params),
            len(func_signature.parameters),
        )
        return False

    # Validate each parameter's name and type
    for (param_name, _), (expected_name, expected_type) in zip(
        func_signature.parameters.items(),
        expected_params,
    ):
        if (
            param_name != expected_name
            or func_hints.get(param_name) != expected_type
        ):
            logger.warning(
                "Expected parameter %s of type %s, but got %s of type %s",
                expected_name,
                expected_type,
                param_name,
                func_hints.get(param_name),
            )
            return False

    # Validate the return type
    return_annotation = func_hints.get("return", None)
    if return_annotation != expected_return:
        logger.warning(
            "Expected return type %s, but got %s",
            expected_return,
            return_annotation,
        )
        return False

    return True
