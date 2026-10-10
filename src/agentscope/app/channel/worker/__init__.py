# -*- coding: utf-8 -*-
"""Out-of-process channel worker entry point.

A platform hands one bot's events to one connection: Feishu, DingTalk
and Slack pick a single connection out of the ones a bot has open, while
Discord broadcasts to all of them. So having every API replica connect
either wastes connections or processes each message several times — the
connections belong in a process whose count is chosen for them rather
than for HTTP traffic.

This worker is that process. It owns:

- a :class:`~agentscope.app.channel.ChannelLifecycleDispatcher`, which
  reconciles the live connections against the stored channel records
  and heartbeats their status;
- a :class:`~agentscope.app.channel.ChannelGateway`, which turns each
  inbound event into a run trigger on the message bus.

It does **not** run agents, serve HTTP, or deliver replies — a reply is
plain REST, so whichever node runs the agent sends it directly. Pair
this with ``create_app(..., enable_channel_worker=False)`` so the API
replicas hold no connections.

This module is a library: a deployment wires its concrete backends
through :func:`run_channel_worker` from whatever bootstrap script it
uses (systemd unit, Kubernetes Deployment, docker-compose ``command``,
...). The shape mirrors :func:`agentscope.app.create_app` — pass
already-constructed backend instances and the worker manages their
lifecycle through an :class:`AsyncExitStack`.

Example::

    import asyncio
    import os

    from agentscope.app.channel import FeishuChannel
    from agentscope.app.channel.worker import run_channel_worker
    from agentscope.app.message_bus import RedisMessageBus
    from agentscope.app.storage import RedisStorage
    from agentscope.app.workspace_manager import LocalWorkspaceManager

    async def main() -> None:
        await run_channel_worker(
            storage=RedisStorage(url=os.environ["REDIS_URL"]),
            message_bus=RedisMessageBus(url=os.environ["REDIS_URL"]),
            workspace_manager=LocalWorkspaceManager(),
            channels=[FeishuChannel],
        )

    if __name__ == "__main__":
        asyncio.run(main())
"""
import asyncio
import signal
from contextlib import AsyncExitStack

from ...._logging import logger
from ...message_bus import MessageBus
from ...storage import StorageBase
from ...workspace_manager import WorkspaceManagerBase
from .._base import ChannelBase
from .._dispatcher import ChannelLifecycleDispatcher
from .._gateway import ChannelGateway
from .._registry import ChannelTypeRegistry


async def run_channel_worker(
    *,
    storage: StorageBase,
    message_bus: MessageBus,
    workspace_manager: WorkspaceManagerBase,
    channels: list[type[ChannelBase]],
) -> None:
    """Hold the channels' long connections until interrupted.

    Args:
        storage (`StorageBase`):
            Persistent storage. Source of truth for which channels to
            run and with which credentials, and where the gateway
            creates the sessions inbound messages land in. Must be a
            backend implementing the channel methods.
        message_bus (`MessageBus`):
            Shared bus. Carries lifecycle notifications in, run
            triggers out, and the per-channel status heartbeat. Must be
            the distributed backend the API replicas use, or they will
            never see this worker's channels.
        workspace_manager (`WorkspaceManagerBase`):
            Assigns each derived session its workspace id under the
            deployment's isolation policy.
        channels (`list[type[ChannelBase]]`):
            Channel classes this worker may run, the same list the API
            process is given — a record whose type is missing here is
            skipped with an error.
    """
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(storage)
        await stack.enter_async_context(message_bus)
        workspace_manager.bind_storage(storage)
        await stack.enter_async_context(workspace_manager)

        dispatcher = ChannelLifecycleDispatcher(
            storage=storage,
            message_bus=message_bus,
            type_registry=ChannelTypeRegistry(channels),
            gateway=ChannelGateway(
                storage=storage,
                message_bus=message_bus,
                workspace_manager=workspace_manager,
            ),
        )
        await stack.enter_async_context(dispatcher.lifespan())
        logger.info("Channel worker ready (%d types)", len(channels))

        # Install handlers on the running loop rather than using
        # ``signal.signal`` so the interaction with asyncio is
        # well-defined — the default handler would raise
        # KeyboardInterrupt at an arbitrary await point.
        loop = asyncio.get_running_loop()
        stop = loop.create_future()

        def _request_stop() -> None:
            """Resolve the shutdown future once."""
            if not stop.done():
                stop.set_result(None)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except NotImplementedError:
                # Windows event loops do not implement this; tests and
                # Linux/macOS deployments are unaffected.
                pass

        try:
            await stop
        finally:
            logger.info("Channel worker shutting down")
