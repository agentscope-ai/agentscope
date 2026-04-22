# -*- coding: utf-8 -*-
"""The skill related classes and functions."""

from ._base import SkillLoaderBase
from ._local_loader import LocalSkillLoader

__all__ = [
    "SkillLoaderBase",
    "LocalSkillLoader",
]
