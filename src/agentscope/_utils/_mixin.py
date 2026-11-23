# -*- coding: utf-8 -*-
"""The mixin for agentscope."""


class DictMixin(dict):
    """The dictionary mixin that allows attribute-style access."""

    __setattr__ = dict.__setitem__

    def __getattr__(self, key: str) -> object:
        """Get a dictionary item through attribute-style access.

        Args:
            key (`str`):
                The requested attribute name.

        Returns:
            `object`:
                The value stored under ``key``.

        Raises:
            `AttributeError`:
                If the dictionary does not contain ``key``.
        """
        try:
            return dict.__getitem__(self, key)
        except KeyError as error:
            raise AttributeError(key) from error

    def __delattr__(self, key: str) -> None:
        """Delete a dictionary item through attribute-style access.

        Args:
            key (`str`):
                The attribute name to remove.

        Raises:
            `AttributeError`:
                If the dictionary does not contain ``key``.
        """
        try:
            dict.__delitem__(self, key)
        except KeyError as error:
            raise AttributeError(key) from error
