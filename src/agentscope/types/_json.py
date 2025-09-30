# -*- coding: utf-8 -*-
"""The JSON related types"""

from typing import Union

JSONPrimitive = Union[
    str,
    int,
    float,
    bool,
    None,
]

JSONSerializableObject = Union[
    JSONPrimitive,
    list["JSONSerializableObject"],
    dict[
        str,
        "JSONSerializableObject",
    ],
]
