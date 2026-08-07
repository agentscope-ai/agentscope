# -*- coding: utf-8 -*-
"""Pluggable credential binding for channel adapters.

QR-code authorization is platform and deployment specific: a Feishu store
application, for example, has a different installation flow from a DingTalk
application.  The channel service therefore owns the HTTP contract while a
provider owns the external authorization session and its secret result.
"""

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, Field

from ..message_bus import MessageBus, MessageBusKeys
from ._errors import ChannelError


class ChannelCredentialBindingState(str, Enum):
    """Public state of a credential-binding session."""

    PENDING = "pending"
    SCANNED = "scanned"
    AUTHORIZED = "authorized"
    EXPIRED = "expired"
    FAILED = "failed"


class ChannelCredentialBindingSession(BaseModel):
    """A newly-created QR-code authorization session.

    ``qr_code_url`` is an image URL (including a ``data:`` URL) rendered by
    the WebUI. Providers should keep credentials server-side; this model is
    intentionally safe to return to an untrusted browser.
    """

    id: str
    qr_code_url: str
    expires_at: str
    state: ChannelCredentialBindingState = (
        ChannelCredentialBindingState.PENDING
    )
    message: str = ""


class ChannelCredentialBindingStatus(BaseModel):
    """Poll response for a credential-binding session."""

    id: str
    state: ChannelCredentialBindingState
    expires_at: str
    message: str = ""


class ChannelCredentialBindingRecord(BaseModel):
    """Private, shared state for a credential-binding session.

    Unlike the public response models, this record may contain credentials.
    It must therefore only be stored in a short-lived server-side registry.
    """

    id: str
    user_id: str
    # Empty keeps records written by an older node readable during a rolling
    # upgrade; providers still reject them and ask the user to retry.
    provider_id: str = ""
    state: ChannelCredentialBindingState
    expires_at: str
    qr_code_url: str = ""
    message: str = ""
    credentials: dict | None = None


class ChannelCredentialBindingStore:
    """TTL-backed binding state shared through the application's bus."""

    _FIELD = "record"
    _OWNER_FIELD = "alive"

    def __init__(self, message_bus: MessageBus) -> None:
        self._bus = message_bus

    @staticmethod
    def _namespace(binding_id: str) -> str:
        return MessageBusKeys.channel_credential_binding(binding_id)

    @staticmethod
    def _owner_namespace(binding_id: str) -> str:
        return MessageBusKeys.channel_credential_binding_owner(binding_id)

    async def put(
        self,
        record: ChannelCredentialBindingRecord,
        ttl_secs: int,
    ) -> None:
        """Create or overwrite a record and refresh its hard TTL."""
        await self._bus.registry_set(
            self._namespace(record.id),
            self._FIELD,
            record.model_dump_json(),
            ttl_secs=max(ttl_secs, 1),
        )

    async def get(
        self,
        binding_id: str,
    ) -> ChannelCredentialBindingRecord | None:
        """Load a binding record, returning ``None`` after TTL expiry."""
        value = await self._bus.registry_get(
            self._namespace(binding_id),
            self._FIELD,
        )
        if value is None:
            return None
        return ChannelCredentialBindingRecord.model_validate_json(value)

    async def replace(
        self,
        record: ChannelCredentialBindingRecord,
        ttl_secs: int,
        expected_states: set[ChannelCredentialBindingState] | None = None,
    ) -> bool:
        """Conditionally replace a record without resurrecting old state."""
        lock = MessageBusKeys.channel_credential_binding_lock(record.id)
        async with self._bus.acquire_lock(lock, ttl_secs=10):
            current = await self.get(record.id)
            if current is None:
                return False
            if current.user_id != record.user_id:
                raise ChannelError("Access denied.", 403)
            if current.provider_id != record.provider_id:
                raise ChannelError(
                    "Credential binding provider does not match. "
                    "Please retry QR binding.",
                    409,
                )
            if (
                expected_states is not None
                and current.state not in expected_states
            ):
                return False
            await self.put(record, ttl_secs)
            return True

    async def delete(
        self,
        user_id: str,
        binding_id: str,
        provider_id: str | None = None,
    ) -> bool:
        """Atomically delete an owned record; absence is idempotent."""
        lock = MessageBusKeys.channel_credential_binding_lock(binding_id)
        async with self._bus.acquire_lock(lock, ttl_secs=10):
            record = await self.get(binding_id)
            if record is None:
                return False
            if record.user_id != user_id:
                raise ChannelError("Access denied.", 403)
            if provider_id is not None and record.provider_id != provider_id:
                raise ChannelError(
                    "Credential binding provider does not match. "
                    "Please retry QR binding.",
                    409,
                )
            await self._bus.registry_drop(self._namespace(binding_id))
            await self._bus.registry_drop(self._owner_namespace(binding_id))
            return True

    async def refresh_owner(self, binding_id: str, ttl_secs: int) -> bool:
        """Refresh the registration worker lease if the session exists."""
        lock = MessageBusKeys.channel_credential_binding_lock(binding_id)
        async with self._bus.acquire_lock(lock, ttl_secs=10):
            record = await self.get(binding_id)
            if record is None or record.state not in {
                ChannelCredentialBindingState.PENDING,
                ChannelCredentialBindingState.SCANNED,
            }:
                return False
            await self._bus.registry_set(
                self._owner_namespace(binding_id),
                self._OWNER_FIELD,
                "1",
                ttl_secs=max(ttl_secs, 1),
            )
            return True

    async def owner_alive(self, binding_id: str) -> bool:
        """Return whether the worker driving external polling is alive."""
        return await self._bus.registry_exists(
            self._owner_namespace(binding_id),
            self._OWNER_FIELD,
        )

    async def replace_if_owner_missing(
        self,
        record: ChannelCredentialBindingRecord,
        ttl_secs: int,
        expected_states: set[ChannelCredentialBindingState],
    ) -> tuple[ChannelCredentialBindingRecord | None, bool]:
        """Replace an active record only when its worker lease is absent.

        The record and owner lease are checked while holding the same
        distributed lock used by :meth:`refresh_owner`. This closes the
        lease-boundary race where a healthy worker renewed its lease after
        another node checked it, but before that node marked the session as
        failed.

        Returns:
            A pair of the current record (or ``None`` if it expired) and a
            flag indicating whether the replacement was applied.
        """
        lock = MessageBusKeys.channel_credential_binding_lock(record.id)
        async with self._bus.acquire_lock(lock, ttl_secs=10):
            current = await self.get(record.id)
            if current is None:
                return None, False
            if current.user_id != record.user_id:
                raise ChannelError("Access denied.", 403)
            if current.provider_id != record.provider_id:
                raise ChannelError(
                    "Credential binding provider does not match. "
                    "Please retry QR binding.",
                    409,
                )
            if current.state not in expected_states:
                return current, False
            owner_alive = await self._bus.registry_exists(
                self._owner_namespace(record.id),
                self._OWNER_FIELD,
            )
            if owner_alive:
                return current, False
            await self.put(record, ttl_secs)
            return record, True

    async def clear_owner(self, binding_id: str) -> None:
        """Remove a registration worker lease."""
        await self._bus.registry_drop(self._owner_namespace(binding_id))


class ChannelCredentialBindingBase(ABC):
    """Platform-specific QR credential authorization provider.

    Implementations must scope every operation to ``user_id`` and make
    ``complete`` idempotent.  ``resolve_credentials`` is called only after a
    poll reports ``authorized`` and must never return secrets from an HTTP
    endpoint directly; the channel service consumes them internally.
    """

    display_name: str = "Scan QR code"
    description: str = "Authorize the channel by scanning a QR code."
    provider_id: str = ""

    async def aclose(self) -> None:
        """Release provider-local background work during app shutdown."""

    @abstractmethod
    async def start(
        self,
        user_id: str,
        store: ChannelCredentialBindingStore,
    ) -> ChannelCredentialBindingSession:
        """Start an external authorization session for ``user_id``."""

    @abstractmethod
    async def get_status(
        self,
        user_id: str,
        binding_id: str,
        store: ChannelCredentialBindingStore,
    ) -> ChannelCredentialBindingStatus:
        """Return the current public state, enforcing ownership."""

    @abstractmethod
    async def resolve_credentials(
        self,
        user_id: str,
        binding_id: str,
        store: ChannelCredentialBindingStore,
    ) -> dict:
        """Return authorized credentials to the channel service only."""

    @abstractmethod
    async def complete(
        self,
        user_id: str,
        binding_id: str,
        store: ChannelCredentialBindingStore,
    ) -> None:
        """Invalidate a successfully consumed binding session."""

    @abstractmethod
    async def cancel(
        self,
        user_id: str,
        binding_id: str,
        store: ChannelCredentialBindingStore,
    ) -> None:
        """Cancel a binding session; must be idempotent."""


class ChannelCredentialMode(BaseModel):
    """Frontend metadata for one credential-acquisition tab."""

    id: str = Field(description="Stable mode identifier.")
    type: str = Field(description="Renderer type: 'manual' or 'qr_code'.")
    display_name: str
    description: str = ""
