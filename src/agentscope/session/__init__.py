# -*- coding: utf-8 -*-
"""The session module in agentscope."""

from ._session_base import SessionBase
from ._json_session import JSONSession

__all__ = [
    "SessionBase",
    "JSONSession",
]
