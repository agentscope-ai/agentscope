# -*- coding: utf-8 -*-
"""Exceptions raised by storage-level session fork operations."""


class SessionForkNotFoundError(LookupError):
    """The requested source session was not found."""


class SessionForkConflictError(RuntimeError):
    """The source session cannot be forked under current conditions."""


class SessionForkCorruptedGraphError(RuntimeError):
    """The source session graph is inconsistent or incomplete."""
