# -*- coding: utf-8 -*-
"""Workflow for agent learning."""

from typing import (
    Dict,
    Callable,
    Awaitable,
    get_type_hints,
)

import inspect


from ..model import TrinityChatModel


WorkflowType = Callable[[Dict, TrinityChatModel], Awaitable[float]]


def validate_function_signature(func: Callable) -> bool:
    """Validate if a function matches the workflow type signature.

    Args:
        func (Callable): The function to validate.
    """
    # check if the function is asynchronous
    if not inspect.iscoroutinefunction(func):
        print("The function is not asynchronous.")
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
        print(
            f"Expected {len(expected_params)} parameters, "
            f"but got {len(func_signature.parameters)}",
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
            print(
                f"Expected parameter {expected_name} of type "
                f"{expected_type}, but got {param_name} of"
                f" type {func_hints.get(param_name)}",
            )
            return False

    # Validate the return type
    return_annotation = func_hints.get("return", None)
    if return_annotation != expected_return:
        print(
            f"Expected return type {expected_return}, "
            f"but got {return_annotation}",
        )
        return False

    return True
