# -*- coding: utf-8 -*-
"""The state module in agentscope."""

import json
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Any, Optional

from ..types import JSONSerializableObject


@dataclass
class _JSONSerializeFunction:
    to_json: Optional[Callable[[Any], Any]] = None
    """The function converting the original data to JSON data."""
    load_json: Optional[Callable[[Any], Any]] = None
    """The function converting the JSON data to original data."""


class StateModule:
    """The state module class in agentscope to support nested state
    serialization and deserialization."""

    def __init__(self) -> None:
        """Initialize the state module."""
        self._module_dict = OrderedDict()
        self._attribute_dict = OrderedDict()

    def __setattr__(self, key: str, value: Any) -> None:
        """Set attributes and record state modules."""
        if isinstance(value, StateModule):
            if not hasattr(self, "_module_dict"):
                raise AttributeError(
                    f"Call the super().__init__() method within the "
                    f"constructor of {self.__class__.__name__} before setting "
                    f"any attributes.",
                )
            self._module_dict[key] = value
        super().__setattr__(key, value)

    def __delattr__(self, key: str) -> None:
        """Delete attributes and remove from state modules."""
        if key in self._module_dict:
            self._module_dict.pop(key)
        if key in self._attribute_dict:
            self._attribute_dict.pop(key)
        super().__delattr__(key)

    def state_dict(self) -> dict:
        """Get the state dictionary of the module, including the nested
        state modules and registered attributes.

        Returns:
            `dict`:
                A dictionary that keys are attribute names and values are
                the state of the attribute.
        """
        state = {}
        for key in self._module_dict:
            attr = getattr(self, key, None)
            if isinstance(attr, StateModule):
                state[key] = attr.state_dict()

        for key in self._attribute_dict:
            attr = getattr(self, key)
            to_json_function = self._attribute_dict[key].to_json
            if to_json_function is not None:
                state[key] = to_json_function(attr)
            else:
                state[key] = attr

        return state

    def load_state_dict(self, state_dict: dict, strict: bool = True) -> None:
        """Load the state dictionary into the module.

        Args:
            state_dict (`dict`):
                The state dictionary to load.
            strict (`bool`, defaults to `True`):
                If `True`, raises an error if any key in the module is not
                found in the state_dict. If `False`, skips missing keys.
        """
        for key in self._module_dict:
            if key not in state_dict:
                if strict:
                    raise KeyError(
                        f"Key '{key}' not found in state_dict. Ensure that "
                        f"the state_dict contains all required keys.",
                    )
                continue
            self._module_dict[key].load_state_dict(state_dict[key])

        for key in self._attribute_dict:
            if key not in state_dict:
                if strict:
                    raise KeyError(
                        f"Key '{key}' not found in state_dict. Ensure that "
                        f"the state_dict contains all required keys.",
                    )
                continue
            from_json_func = self._attribute_dict[key].load_json
            if from_json_func is not None:
                setattr(self, key, from_json_func(state_dict[key]))
            else:
                setattr(self, key, state_dict[key])

    def register_state(
        self,
        attr_name: str,
        custom_to_json: Callable[[Any], JSONSerializableObject] | None = None,
        custom_from_json: Callable[[JSONSerializableObject], Any]
        | None = None,
    ) -> None:
        """Register an attribute to be tracked as a state variable.

        Args:
            attr_name (`str`):
                The name of the attribute to register.
            custom_to_json (`Callable[[Any], JSONSerializableObject] | None`, \
            optional):
                A custom function to convert the attribute to a
                JSON-serializable format. If not provided, `json.dumps` will
                be used.
            custom_from_json (`Callable[[JSONSerializableObject], Any] | None`\
            , defaults to `None`):
                A custom function to convert the JSON dictionary back to the
                original attribute format.
        """
        attr = getattr(self, attr_name)

        if custom_to_json is None:
            # Make sure the attribute is JSON serializable natively
            try:
                json.dumps(attr)
            except Exception as e:
                raise TypeError(
                    f"Attribute '{attr_name}' is not JSON serializable. "
                    "Please provide a custom function to convert the "
                    "attribute to a JSON-serializable format.",
                ) from e

        if attr_name in self._module_dict:
            raise ValueError(
                f"Attribute `{attr_name}` is already registered as a module. ",
            )

        self._attribute_dict[attr_name] = _JSONSerializeFunction(
            to_json=custom_to_json,
            load_json=custom_from_json,
        )
