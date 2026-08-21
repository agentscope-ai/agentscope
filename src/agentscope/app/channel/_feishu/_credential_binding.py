# -*- coding: utf-8 -*-
"""Feishu one-click app registration through OAuth Device Flow."""

import asyncio
from datetime import datetime, timedelta, timezone
import inspect
import secrets
from types import ModuleType
from typing import Any

from ...._logging import logger
from .._credential_binding import (
    ChannelCredentialBindingBase,
    ChannelCredentialBindingRecord,
    ChannelCredentialBindingSession,
    ChannelCredentialBindingState,
    ChannelCredentialBindingStore,
    ChannelCredentialBindingStatus,
)
from .._errors import ChannelError


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp, treating legacy naive values as UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _verification_url_to_qr_data_url(verification_url: str) -> str:
    """Render a verification URL as an embeddable SVG data URL."""
    try:
        # pylint: disable=import-outside-toplevel
        import segno  # type: ignore[import-untyped]

        # pylint: enable=import-outside-toplevel
    except ImportError as exc:
        raise ChannelError(
            "Feishu QR binding requires the 'segno' package "
            "(pip install 'agentscope[channel]').",
            503,
        ) from exc

    # ``make_qr`` deliberately disables Segno's automatic Micro QR choice:
    # authorization URLs should use the universally supported QR format.
    return segno.make_qr(verification_url).svg_data_uri(scale=8, border=2)


class FeishuCredentialBinding(ChannelCredentialBindingBase):
    """Create Feishu/Lark applications with the official Python SDK.

    Feishu's ``aregister_app`` runs the polling loop itself. This adapter
    starts it as a background task and exposes only its QR/status to the
    browser. Private state is stored through ``MessageBus`` with a hard TTL,
    so a Redis-backed deployment supports non-sticky multi-worker requests.
    The SDK does not expose its device code for restart recovery; a shared
    record therefore remains retryable from any worker while the SDK polling
    task stays pinned to the process that started it.
    """

    display_name = "Scan QR code"
    description = "Scan with Feishu or Lark to create and authorize the bot."
    provider_id = "feishu"

    def __init__(
        self,
        app_preset: dict | None = None,
        addons: dict | None = None,
        authorized_ttl_secs: int = 300,
        terminal_ttl_secs: int = 60,
    ) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._authorized_ttl_secs = max(authorized_ttl_secs, 1)
        self._terminal_ttl_secs = max(terminal_ttl_secs, 1)
        self._app_preset = app_preset or {
            "name": "{user}'s AgentScope bot",
            "desc": "Created by AgentScope",
        }
        self._addons = addons or {
            "scopes": {
                "tenant": [
                    "im:message",
                    "im:message:send_as_bot",
                    "im:message.p2p_msg:readonly",
                    "im:message.group_at_msg:readonly",
                    "im:resource",
                    "im:chat:readonly",
                    "im:message.reactions:write",
                    "cardkit:card:write",
                ],
            },
            "events": {
                "items": {"tenant": ["im.message.receive_v1"]},
            },
            "callbacks": {"items": ["card.action.trigger"]},
        }

    @staticmethod
    def _sdk() -> ModuleType:
        """Load a registration-capable version of the optional SDK."""
        try:
            # pylint: disable=import-outside-toplevel
            import lark_oapi as lark  # type: ignore[import-untyped]

            # pylint: enable=import-outside-toplevel
        except ImportError as exc:
            raise ChannelError(
                "Feishu QR binding requires lark-oapi>=1.7.2 "
                "(pip install 'agentscope[channel]').",
                503,
            ) from exc
        register_app = getattr(lark, "aregister_app", None)
        if (
            register_app is None
            or "addons"
            not in inspect.signature(
                register_app,
            ).parameters
        ):
            raise ChannelError(
                "Feishu QR binding requires lark-oapi>=1.7.2.",
                503,
            )
        return lark

    async def start(
        self,
        user_id: str,
        store: ChannelCredentialBindingStore,
    ) -> ChannelCredentialBindingSession:
        """Start official one-click registration and wait for its QR URL."""
        sdk = self._sdk()
        binding_id = secrets.token_urlsafe(24)
        record = ChannelCredentialBindingRecord(
            id=binding_id,
            user_id=user_id,
            provider_id=self.provider_id,
            state=ChannelCredentialBindingState.PENDING,
            expires_at=(_utc_now() + timedelta(minutes=5)).isoformat(),
        )
        qr_ready = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(
            self._register(sdk, record, store, qr_ready),
        )
        self._tasks[binding_id] = task

        def remove_task(completed: asyncio.Task[None]) -> None:
            self._tasks.pop(binding_id, None)
            if completed.cancelled():
                return
            exception = completed.exception()
            if exception is not None:
                logger.error(
                    "Feishu credential binding task '%s' failed.",
                    binding_id,
                    exc_info=(
                        type(exception),
                        exception,
                        exception.__traceback__,
                    ),
                )

        task.add_done_callback(remove_task)
        try:
            await asyncio.wait_for(asyncio.shield(qr_ready), timeout=30)
        except asyncio.TimeoutError as exc:
            await self.cancel(user_id, binding_id, store)
            raise ChannelError(
                "Timed out while requesting a Feishu QR code.",
                504,
            ) from exc
        except asyncio.CancelledError:
            await self.cancel(user_id, binding_id, store)
            raise
        except Exception:
            await self.cancel(user_id, binding_id, store)
            raise

        return ChannelCredentialBindingSession(
            id=binding_id,
            qr_code_url=record.qr_code_url,
            expires_at=record.expires_at,
            state=record.state,
            message=record.message,
        )

    async def _register(  # pylint: disable=too-many-statements
        self,
        sdk: ModuleType,
        record: ChannelCredentialBindingRecord,
        store: ChannelCredentialBindingStore,
        qr_ready: asyncio.Future[None],
    ) -> None:
        """Run the SDK registration coroutine and persist short-lived state."""
        loop = asyncio.get_running_loop()

        async def persist_qr(snapshot: ChannelCredentialBindingRecord) -> None:
            expire_in = max(
                int(
                    (
                        _parse_timestamp(snapshot.expires_at) - _utc_now()
                    ).total_seconds(),
                ),
                1,
            )
            try:
                await store.create(
                    snapshot,
                    expire_in + self._terminal_ttl_secs,
                )
            except Exception as exc:  # pylint: disable=broad-except
                if not qr_ready.done():
                    qr_ready.set_exception(exc)
                return
            if not qr_ready.done():
                qr_ready.set_result(None)

        def handle_qr_code(info: dict) -> None:
            if record.qr_code_url or qr_ready.done():
                return
            expire_in = max(int(info.get("expire_in", 300)), 1)
            record.expires_at = (
                _utc_now() + timedelta(seconds=expire_in)
            ).isoformat()
            try:
                record.qr_code_url = _verification_url_to_qr_data_url(
                    str(info["url"]),
                )
            except Exception:
                logger.warning(
                    "Failed to create QR code for binding '%s'.",
                    record.id,
                    exc_info=True,
                )
                if not qr_ready.done():
                    qr_ready.set_exception(
                        ChannelError(
                            "Failed to create the Feishu QR code.",
                            502,
                        ),
                    )
                return
            record.message = "Waiting for authorization in Feishu or Lark."
            asyncio.create_task(
                persist_qr(record.model_copy(deep=True)),
            )

        def on_qr_code(info: dict) -> None:
            loop.call_soon_threadsafe(handle_qr_code, dict(info))

        try:
            result: dict[str, Any] = await sdk.aregister_app(
                on_qr_code=on_qr_code,
                source="agentscope",
                app_preset=self._app_preset,
                addons=self._addons,
            )
            await asyncio.shield(qr_ready)
            record.credentials = {
                "app_id": str(result["client_id"]),
                "app_secret": str(result["client_secret"]),
            }
            record.state = ChannelCredentialBindingState.AUTHORIZED
            record.expires_at = (
                _utc_now() + timedelta(seconds=self._authorized_ttl_secs)
            ).isoformat()
            record.message = "Feishu application created successfully."
            saved = await store.replace(
                record,
                self._authorized_ttl_secs,
                {
                    ChannelCredentialBindingState.PENDING,
                },
            )
            if not saved:
                logger.info(
                    "Discarded authorization result for inactive binding "
                    "'%s'.",
                    record.id,
                )
        except asyncio.CancelledError:
            if not qr_ready.done():
                qr_ready.cancel()
            raise
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Feishu credential binding '%s' failed.",
                record.id,
                exc_info=True,
            )
            name = type(exc).__name__
            if name == "AppExpiredError":
                record.state = ChannelCredentialBindingState.EXPIRED
                record.message = (
                    "The Feishu credential binding expired. Please retry."
                )
            else:
                record.state = ChannelCredentialBindingState.FAILED
                record.message = "Feishu authorization failed. Please retry."
            record.credentials = None
            record.expires_at = (
                _utc_now() + timedelta(seconds=self._terminal_ttl_secs)
            ).isoformat()
            saved = await store.replace(
                record,
                self._terminal_ttl_secs,
                {
                    ChannelCredentialBindingState.PENDING,
                },
            )
            if not saved:
                logger.info(
                    "Discarded failure result for inactive binding '%s'.",
                    record.id,
                )
            if not qr_ready.done():
                qr_ready.set_exception(
                    ChannelError(record.message, 502),
                )

    async def _owned(
        self,
        user_id: str,
        binding_id: str,
        store: ChannelCredentialBindingStore,
    ) -> ChannelCredentialBindingRecord:
        """Return an owned binding session or a safe client error."""
        record = await store.get(binding_id)
        if record is None:
            raise ChannelError("Credential binding session not found.", 404)
        if record.user_id != user_id:
            raise ChannelError("Access denied.", 403)
        if record.provider_id != self.provider_id:
            raise ChannelError(
                "Credential binding provider does not match. "
                "Please retry QR binding.",
                409,
            )
        return record

    async def get_status(
        self,
        user_id: str,
        binding_id: str,
        store: ChannelCredentialBindingStore,
    ) -> ChannelCredentialBindingStatus:
        """Return the current public status for an owned binding."""
        record = await self._owned(user_id, binding_id, store)
        active_states = {ChannelCredentialBindingState.PENDING}
        if record.state in active_states | {
            ChannelCredentialBindingState.AUTHORIZED,
        } and _utc_now() >= _parse_timestamp(record.expires_at):
            record.state = ChannelCredentialBindingState.EXPIRED
            record.credentials = None
            record.message = "The Feishu credential binding expired."
            record.expires_at = (
                _utc_now() + timedelta(seconds=self._terminal_ttl_secs)
            ).isoformat()
            await store.replace(
                record,
                self._terminal_ttl_secs,
                active_states | {ChannelCredentialBindingState.AUTHORIZED},
            )
            task = self._tasks.get(binding_id)
            if task is not None:
                task.cancel()
        return ChannelCredentialBindingStatus(
            id=binding_id,
            state=record.state,
            expires_at=record.expires_at,
            message=record.message,
        )

    async def resolve_credentials(
        self,
        user_id: str,
        binding_id: str,
        store: ChannelCredentialBindingStore,
    ) -> dict:
        """Return credentials only after successful authorization."""
        record = await self._owned(user_id, binding_id, store)
        if _utc_now() >= _parse_timestamp(record.expires_at):
            record.state = ChannelCredentialBindingState.EXPIRED
            record.credentials = None
            record.message = "The Feishu credential binding expired."
            record.expires_at = (
                _utc_now() + timedelta(seconds=self._terminal_ttl_secs)
            ).isoformat()
            await store.replace(
                record,
                self._terminal_ttl_secs,
                {ChannelCredentialBindingState.AUTHORIZED},
            )
            raise ChannelError(
                "Credential binding is not authorized.",
                409,
            )
        if (
            record.state is not ChannelCredentialBindingState.AUTHORIZED
            or record.credentials is None
        ):
            raise ChannelError(
                "Credential binding is not authorized.",
                409,
            )
        return dict(record.credentials)

    async def complete(
        self,
        user_id: str,
        binding_id: str,
        store: ChannelCredentialBindingStore,
    ) -> None:
        """Consume a successful session and erase its secret result."""
        await store.delete(user_id, binding_id, self.provider_id)
        task = self._tasks.get(binding_id)
        if task is not None and not task.done():
            task.cancel()

    async def cancel(
        self,
        user_id: str,
        binding_id: str,
        store: ChannelCredentialBindingStore,
    ) -> None:
        """Cancel an unfinished session; repeated cancellation is harmless."""
        await store.delete(user_id, binding_id, self.provider_id)
        task = self._tasks.get(binding_id)
        if task is not None and not task.done():
            task.cancel()

    async def aclose(self) -> None:
        """Cancel and await all SDK polling tasks owned by this process."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
