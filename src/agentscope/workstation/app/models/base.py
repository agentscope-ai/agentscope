# -*- coding: utf-8 -*-
"""The base model"""
from typing import Any
from sqlalchemy import TypeDecorator, Integer


class IntEnum(TypeDecorator):
    """IntEnum"""

    impl = Integer

    def __init__(self, enum_type: Any) -> None:
        """init"""
        super().__init__()
        self.enum_type = enum_type

    def process_bind_param(
        self,
        value: Any,
        dialect: Any,  # pylint: disable=unused-argument
    ) -> Any:
        """process_bind_param"""
        if isinstance(value, self.enum_type):
            return value.value
        return value

    def process_result_value(
        self,
        value: Any,
        dialect: Any,  # pylint: disable=unused-argument
    ) -> Any:
        """process_result_value"""
        return self.enum_type(value)
