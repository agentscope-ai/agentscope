# -*- coding: utf-8 -*-
"""The mixin for agentscope."""
from typing import Any


class DictMixin(dict):
    """The dictionary mixin that allows attribute-style access."""

    __setattr__ = dict.__setitem__

    def __getattr__(self, name: str) -> Any:
        """Return a mapped value or signal a missing attribute."""
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc
