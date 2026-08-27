# -*- coding: utf-8 -*-
"""Asyncio helpers shared across AgentScope."""
import asyncio
import sys


def ensure_windows_proactor_event_loop_policy() -> None:
    """Use an event loop policy that supports subprocesses on Windows.

    :class:`~agentscope.tool.LocalBackend` and several workspace code paths
    spawn subprocesses via ``asyncio.create_subprocess_exec``. On Windows the
    legacy :class:`asyncio.SelectorEventLoop` raises
    :exc:`NotImplementedError` for subprocess transport, while
    :class:`asyncio.ProactorEventLoop` (selected by
    :class:`asyncio.WindowsProactorEventLoopPolicy`) does not.

    Uvicorn and some test runners may install a selector-based loop before
    AgentScope code runs. Call this **before** the running loop is created —
    for example at app factory time or when importing the local backend.
    """
    if sys.platform != "win32":
        return

    policy = asyncio.get_event_loop_policy()
    if isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
        return

    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
