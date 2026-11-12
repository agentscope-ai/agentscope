# -*- coding: utf-8 -*-
"""Execution ownership independent of the application storage backend."""

from typing import Protocol


class CodeModeExecutionGuard(Protocol):
    """Own a session until its script and host calls have finished.

    Acquisition must be atomic and fail closed on ambiguous storage errors.
    Ownership must not expire merely because a time budget elapsed. Release
    must compare the token so a late owner cannot remove another execution.
    """

    async def acquire(self, session_id: str) -> str:
        """Claim the session or raise without allowing execution."""

    async def release(self, session_id: str, token: str) -> None:
        """Conditionally release this execution's ownership."""
