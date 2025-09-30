# -*- coding: utf-8 -*-
"""The agent hooks types."""
from typing import Literal

AgentHookTypes = (
    str
    | Literal[
        "pre_reply",
        "post_reply",
        "pre_print",
        "post_print",
        "pre_observe",
        "post_observe",
    ]
)

ReActAgentHookTypes = (
    AgentHookTypes
    | Literal[
        "pre_reasoning",
        "post_reasoning",
        "pre_acting",
        "post_acting",
    ]
)
