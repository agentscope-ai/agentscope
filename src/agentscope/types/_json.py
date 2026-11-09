# -*- coding: utf-8 -*-
"""The JSON related types"""
from typing import TypeAlias

JSONPrimitive: TypeAlias = str | int | float | bool | None

JSONSerializableObject: TypeAlias = (
    JSONPrimitive
    | list["JSONSerializableObject"]
    | dict[
        str,
        "JSONSerializableObject",
    ]
)
