# -*- coding: utf-8 -*-
"""The agentscope serialization module"""
import warnings

from ._logging import (
    logger,
    setup_logger,
)
from ._version import __version__

# Raise each warning only once
warnings.filterwarnings("once", category=DeprecationWarning)


__all__ = [
    "logger",
    "setup_logger",
    "__version__",
]
