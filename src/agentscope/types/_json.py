# -*- coding: utf-8 -*-
"""The JSON related types"""

JSONPrimitive = str | int | float | bool | None

JSONSerializableObject = (
    JSONPrimitive
    | list["JSONSerializableObject"]
    | dict[str, "JSONSerializableObject"]
)
