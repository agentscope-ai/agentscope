# -*- coding: utf-8 -*-
"""The pipeline module in AgentScope, that provides syntactic sugar for
complex workflows and multi-agent conversations."""

from ._msghub import MsgHub
from ._class import SequentialPipeline, FanoutPipeline
from ._functional import (
    sequential_pipeline,
    fanout_pipeline,
    stream_printing_messages,
)
from ._chat_room import ChatRoom
from .branching_pipeline import (
    IfElsePipeline,
    SwitchPipeline,
    ParallelBranchPipeline,
)

__all__ = [
    "MsgHub",
    "SequentialPipeline",
    "sequential_pipeline",
    "FanoutPipeline",
    "fanout_pipeline",
    "stream_printing_messages",
    "ChatRoom",
    "IfElsePipeline",
    "SwitchPipeline",
    "ParallelBranchPipeline",
]
