# -*- coding: utf-8 -*-
"""Compatibility layer: Re-export GraphKnowledgeBase.

This module maintains backward compatibility by re-exporting the
refactored GraphKnowledgeBase class from the _graph submodule.
"""

from ._graph import GraphKnowledgeBase

__all__ = ["GraphKnowledgeBase"]
