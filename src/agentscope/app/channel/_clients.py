# -*- coding: utf-8 -*-
"""Channel instances for processes that do not hold the connection.

A channel has exactly one piece of connection-bound state: the inbound
long connection opened by ``start_listening``. Everything else — sending
a reply, adding a reaction, listing chats, the platform tools handed to
the agent — is plain REST against the platform, needing only the stored
credentials.

So a process that never connects can still do all of it: build the same
channel class from its record and simply never call ``start_listening``.
That is what this factory does, and it is what lets the long connection
live in one worker while runs execute on any node.

Instances are cached per channel and rebuilt when the record's
``updated_at`` moves, so a credential rotation takes effect without a
restart. A cached instance is shared by concurrent runs — channels must
therefore keep no state across calls.

A replaced instance is retired rather than closed: a run that borrowed
it still holds it — its platform tools stay callable for the whole turn
— and closing the HTTP client underneath would break them. Retired
instances are released when the
factory shuts down, which bounds what a rotation costs to one idle
client per rotation.
"""
from types import TracebackType

from ..._logging import logger
from ..storage import StorageBase
from ._base import ChannelBase
from ._registry import ChannelTypeRegistry


class ChannelClients:
    """Builds and caches unconnected channel instances by channel id."""

    def __init__(
        self,
        storage: StorageBase,
        type_registry: ChannelTypeRegistry,
    ) -> None:
        """Bind storage and the registry that knows the channel classes.

        Args:
            storage (`StorageBase`): Source of channel records.
            type_registry (`ChannelTypeRegistry`): Builds instances from
                a record's type, credentials and config.
        """
        self._storage = storage
        self._types = type_registry
        self._cache: dict[str, tuple[str, ChannelBase]] = {}
        self._retired: list[ChannelBase] = []

    async def __aenter__(self) -> "ChannelClients":
        """Enter the factory's lifecycle; nothing is built up front."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release every instance this factory built.

        Nothing is borrowing them by now: the runs that could have are
        torn down with the process.
        """
        for channel_id in list(self._cache):
            self._retire(channel_id)
        for channel in self._retired:
            try:
                await channel.aclose()
            except Exception:  # pylint: disable=broad-except
                logger.warning("a channel client did not close cleanly")
        self._retired.clear()

    def _retire(self, channel_id: str) -> None:
        """Drop a cached instance without closing it.

        A concurrent run may still hold this instance through the
        platform tools attached to its toolkit, so closing here would
        pull the connection out from under an in-flight call. It is
        released at shutdown instead.

        Args:
            channel_id (`str`): The channel whose instance to retire.
        """
        cached = self._cache.pop(channel_id, None)
        if cached is not None:
            self._retired.append(cached[1])

    async def get(self, channel_id: str) -> ChannelBase | None:
        """Return an instance for ``channel_id``, or ``None``.

        Never calls ``start_listening``, so this opens no connection and
        starts no background task — only the platform's REST surface is
        usable on the returned instance.

        Args:
            channel_id (`str`): The channel to build a client for.

        Returns:
            `ChannelBase | None`: The instance, or ``None`` when the
            record is gone, disabled, or its type is not registered.
        """
        record = await self._storage.get_channel(channel_id)
        if record is None or not record.enabled:
            self._retire(channel_id)
            return None

        version = str(record.updated_at)
        cached = self._cache.get(channel_id)
        if cached is not None and cached[0] == version:
            return cached[1]

        try:
            channel = self._types.create_channel(
                channel_type=record.channel_type,
                channel_id=record.id,
                credentials=record.credentials,
                config=record.platform_config,
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "channel client '%s' could not be built",
                channel_id,
            )
            return None

        # Retire the rotated instance; borrowers keep working.
        self._retire(channel_id)
        self._cache[channel_id] = (version, channel)
        return channel
