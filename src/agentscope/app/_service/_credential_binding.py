# -*- coding: utf-8 -*-
"""Drive channel credential-binding sessions from any replica.

A binding session outlives the request that opened it but not by much,
and its steps are spread across whichever replicas the operator's client
happens to reach. So the session lives entirely in the message bus and
this service keeps nothing: every method reads the record, does one
thing, and writes it back under compare-and-set.

That is also why there is no background task polling the platform. The
client already polls us for the session's status, so that request is
what advances it — nothing outlives it, nothing needs an owner, and a
replica dying mid-session costs the operator one retry.
"""
import json
import secrets
import time
from typing import Any

from pydantic import BaseModel, Field

from .._bus_ops import MessageBusKeys
from ..channel import BindingState, ChannelTypeRegistry
from ..message_bus import MessageBus


class BindingSession(BaseModel):
    """One credential-binding session, as stored in the bus.

    Holds the platform's credentials once obtained. They are in the
    clear, as they are in :class:`~agentscope.app.storage.ChannelRecord`
    once the channel exists — at-rest encryption is a future hook for
    both. Here the exposure is bounded by
    :attr:`MessageBusKeys.CREDENTIAL_BINDING_CLAIM_TTL_SECS` and by
    being claimable only once.
    """

    user_id: str
    channel_type: str
    state: BindingState = BindingState.PENDING
    verification_url: str = ""
    error: str = ""
    credentials: dict[str, Any] = Field(default_factory=dict)
    provider_state: dict[str, Any] = Field(default_factory=dict)
    retry_after_secs: int = 5
    last_stepped_at: float = 0.0


class BindingView(BaseModel):
    """What a client is allowed to see while a session is open."""

    binding_id: str
    state: BindingState
    verification_url: str = ""
    error: str = ""
    retry_after_secs: int = 5


class CredentialBindingError(Exception):
    """A binding request that maps onto an HTTP status."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        """Carry the status the router should answer with.

        Args:
            message (`str`): Operator-facing detail.
            status_code (`int`, defaults to ``400``): HTTP status.
        """
        super().__init__(message)
        self.status_code = status_code


class CredentialBindingService:
    """Open, advance, and claim credential-binding sessions."""

    def __init__(
        self,
        message_bus: MessageBus,
        type_registry: ChannelTypeRegistry,
    ) -> None:
        """Bind the dependencies; the service holds no session state.

        Args:
            message_bus (`MessageBus`):
                Where sessions live, so any replica can serve any step.
            type_registry (`ChannelTypeRegistry`):
                Resolves a channel type to its binding provider.
        """
        self._bus = message_bus
        self._types = type_registry

    async def start(self, user_id: str, channel_type: str) -> BindingView:
        """Open a session and return where the operator must go.

        Args:
            user_id (`str`): The operator; only they may drive it.
            channel_type (`str`): The platform to bind.

        Returns:
            `BindingView`: The new session.

        Raises:
            `CredentialBindingError`: The type is unknown or offers no
                interactive binding.
        """
        provider = self._provider(channel_type)
        step = await provider.begin()

        session = BindingSession(
            user_id=user_id,
            channel_type=channel_type,
            state=step.state,
            verification_url=step.verification_url,
            error=step.error,
            credentials=step.credentials,
            provider_state=step.provider_state,
            retry_after_secs=(
                5 if step.retry_after_secs is None else step.retry_after_secs
            ),
        )
        binding_id = secrets.token_urlsafe(24)
        await self._bus.registry_set(
            MessageBusKeys.channel_credential_binding(binding_id),
            MessageBusKeys.CREDENTIAL_BINDING_FIELD,
            session.model_dump_json(),
            ttl_secs=step.expires_in_secs,
        )
        return self._view(binding_id, session)

    async def poll(self, user_id: str, binding_id: str) -> BindingView:
        """Report the session, advancing it by one step when due.

        Args:
            user_id (`str`): Must own the session.
            binding_id (`str`): The session to report.

        Returns:
            `BindingView`: The session's state after this call.

        Raises:
            `CredentialBindingError`: Unknown session (404) or not the
                caller's (403).
        """
        raw, session = await self._load(user_id, binding_id)

        # Terminal, or asked again sooner than the platform allows: the
        # stored record is already the answer.
        if session.state.is_terminal or (
            time.time() - session.last_stepped_at < session.retry_after_secs
        ):
            return self._view(binding_id, session)

        # Claim the upstream call before making it. Two replicas polled
        # at once would otherwise both pass the check above and both ask
        # the platform, doubling a rate the platform sets.
        session.last_stepped_at = time.time()
        reserved = session.model_dump_json()
        if not await self._bus.registry_set_if(
            MessageBusKeys.channel_credential_binding(binding_id),
            MessageBusKeys.CREDENTIAL_BINDING_FIELD,
            reserved,
            expected=raw,
        ):
            _, session = await self._load(user_id, binding_id)
            return self._view(binding_id, session)

        step = await self._provider(session.channel_type).advance(
            session.provider_state,
        )
        session.state = step.state
        session.error = step.error
        session.credentials = step.credentials or session.credentials
        session.provider_state = step.provider_state or session.provider_state
        if step.retry_after_secs is not None:
            session.retry_after_secs = step.retry_after_secs

        # A losing write means the operator cancelled while we were
        # asking the platform. Drop this result rather than reviving it.
        if not await self._bus.registry_set_if(
            MessageBusKeys.channel_credential_binding(binding_id),
            MessageBusKeys.CREDENTIAL_BINDING_FIELD,
            session.model_dump_json(),
            expected=reserved,
            ttl_secs=(
                MessageBusKeys.CREDENTIAL_BINDING_CLAIM_TTL_SECS
                if session.state is BindingState.AUTHORIZED
                else None
            ),
        ):
            _, session = await self._load(user_id, binding_id)

        return self._view(binding_id, session)

    async def cancel(self, user_id: str, binding_id: str) -> None:
        """Abandon a session from whichever replica took the request.

        Args:
            user_id (`str`): Must own the session.
            binding_id (`str`): The session to abandon.

        Raises:
            `CredentialBindingError`: Unknown session or not the
                caller's.
        """
        while True:
            raw, session = await self._load(user_id, binding_id)

            # Approved but unclaimed: the operator walked away from
            # credentials that would otherwise stay claimable until the
            # TTL. Take them off the record instead of leaving them.
            if session.state is BindingState.AUTHORIZED:
                await self._bus.registry_pop(
                    MessageBusKeys.channel_credential_binding(binding_id),
                    MessageBusKeys.CREDENTIAL_BINDING_FIELD,
                )
                return

            if session.state.is_terminal:
                return

            session.state = BindingState.CANCELLED
            session.credentials = {}
            # Losing means a poll wrote first; re-read and insist, or
            # this would report success on a session that stays live.
            if await self._bus.registry_set_if(
                MessageBusKeys.channel_credential_binding(binding_id),
                MessageBusKeys.CREDENTIAL_BINDING_FIELD,
                session.model_dump_json(),
                expected=raw,
            ):
                return

    async def claim(
        self,
        user_id: str,
        binding_id: str,
        channel_type: str,
    ) -> dict[str, Any]:
        """Take the credentials, consuming the session.

        Args:
            user_id (`str`): Must own the session.
            binding_id (`str`): The session to claim.
            channel_type (`str`): The type the channel is being created
                as; must match what was bound.

        Returns:
            `dict[str, Any]`: The platform credentials.

        Raises:
            `CredentialBindingError`: Unknown, not the caller's, for a
                different type, or not authorized yet.
        """
        # Check before taking: a pop first would let anyone holding an
        # id destroy someone else's session, and would burn the
        # operator's own on a mismatched type or an early claim.
        _, session = await self._load(user_id, binding_id)
        if session.channel_type != channel_type:
            raise CredentialBindingError(
                f"Binding is for channel type '{session.channel_type}'.",
                409,
            )
        if session.state is not BindingState.AUTHORIZED:
            raise CredentialBindingError(
                f"Binding is {session.state.value}, not authorized.",
                409,
            )

        # ``AUTHORIZED`` is terminal, so the record cannot have changed
        # underneath — but two create requests can still race here, and
        # only the one that takes it gets the credentials.
        raw = await self._bus.registry_pop(
            MessageBusKeys.channel_credential_binding(binding_id),
            MessageBusKeys.CREDENTIAL_BINDING_FIELD,
        )
        if raw is None:
            raise CredentialBindingError("Binding not found.", 404)
        return BindingSession.model_validate(json.loads(raw)).credentials

    def _provider(self, channel_type: str) -> Any:
        """Resolve a channel type's binding provider.

        Args:
            channel_type (`str`): The platform type.

        Returns:
            `CredentialBindingBase`: Its provider.

        Raises:
            `CredentialBindingError`: Unknown type, or one that only
                supports pasting credentials in.
        """
        channel_cls = self._types.get(channel_type)
        if channel_cls is None:
            raise CredentialBindingError(
                f"Channel type '{channel_type}' is not registered.",
                404,
            )
        if channel_cls.credential_binding is None:
            raise CredentialBindingError(
                f"Channel type '{channel_type}' has no interactive "
                f"credential binding.",
                400,
            )
        return channel_cls.credential_binding()

    async def _load(
        self,
        user_id: str,
        binding_id: str,
    ) -> tuple[str, BindingSession]:
        """Read a session, returning the raw value for compare-and-set.

        Args:
            user_id (`str`): The caller.
            binding_id (`str`): The session to read.

        Returns:
            `tuple[str, BindingSession]`: The stored string and its
            parsed form.

        Raises:
            `CredentialBindingError`: Absent, expired, or another
                user's — all reported as 404 so a session id cannot be
                probed.
        """
        raw = await self._bus.registry_get(
            MessageBusKeys.channel_credential_binding(binding_id),
            MessageBusKeys.CREDENTIAL_BINDING_FIELD,
        )
        if raw is None:
            raise CredentialBindingError("Binding not found.", 404)

        session = BindingSession.model_validate(json.loads(raw))
        if session.user_id != user_id:
            raise CredentialBindingError("Binding not found.", 404)
        return raw, session

    @staticmethod
    def _view(binding_id: str, session: BindingSession) -> BindingView:
        """Strip a session down to what a client may see.

        Args:
            binding_id (`str`): The session id.
            session (`BindingSession`): The stored session.

        Returns:
            `BindingView`: Without credentials or provider state.
        """
        return BindingView(
            binding_id=binding_id,
            state=session.state,
            verification_url=session.verification_url,
            error=session.error,
            retry_after_secs=session.retry_after_secs,
        )
