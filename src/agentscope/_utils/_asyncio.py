# -*- coding: utf-8 -*-
"""Asyncio helpers shared across AgentScope."""
import asyncio
import sys


def ensure_windows_proactor_event_loop_policy() -> None:
    """Use an event loop policy that supports subprocesses on Windows.

    Per the `Python asyncio platform notes
    <https://docs.python.org/3/library/asyncio-platforms.html#windows>`_,
    :class:`asyncio.SelectorEventLoop` does **not** support subprocesses on
    Windows, while :class:`asyncio.ProactorEventLoop` does.
    :class:`asyncio.WindowsProactorEventLoopPolicy` has been the default
    since Python 3.8, but uvicorn's reload mode (and some test runners) may
    replace it with :class:`asyncio.WindowsSelectorEventLoopPolicy`.

    :class:`~agentscope.tool.LocalBackend` and local workspace code paths
    spawn subprocesses via ``asyncio.create_subprocess_exec``. Call this
    helper **before** the running loop is created — for example in
    :func:`~agentscope.app.create_app` or when importing the local backend.
    """
    if sys.platform != "win32":
        return

    policy = asyncio.get_event_loop_policy()
    if isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
        return

    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
