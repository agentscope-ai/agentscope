# -*- coding: utf-8 -*-
"""The A2A related modules."""
from ._base import AgentCardResolverBase
from ._file_resolver import FileAgentCardResolver
from ._well_known_resolver import WellKnownAgentCardResolver
from ._nacos_resolver import NacosAgentCardResolver


__all__ = [
    "AgentCardResolverBase",
    "FileAgentCardResolver",
    "WellKnownAgentCardResolver",
    "NacosAgentCardResolver",
]
