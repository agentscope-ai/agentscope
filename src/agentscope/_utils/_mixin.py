# -*- coding: utf-8 -*-
"""The mixin for agentscope."""


class DictMixin(dict):
    """The dictionary mixin that allows attribute-style access."""

    __setattr__ = dict.__setitem__
    __getattr__ = dict.__getitem__
