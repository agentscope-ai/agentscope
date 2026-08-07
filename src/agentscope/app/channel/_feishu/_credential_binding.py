# -*- coding: utf-8 -*-
"""Feishu one-click app registration through OAuth Device Flow."""

import asyncio
from datetime import datetime, timedelta
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
    worker lease therefore turns an interrupted pending flow into an explicit
    retryable failure instead of leaving it apparently pending until expiry.
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
        owner_lease_secs: int = 15,
    ) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._authorized_ttl_secs = max(authorized_ttl_secs, 1)
        self._terminal_ttl_secs = max(terminal_ttl_secs, 1)
        self._owner_lease_secs = max(owner_lease_secs, 1)
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
            expires_at=(datetime.now() + timedelta(minutes=5)).isoformat(),
        )
        await store.put(record, 300 + self._terminal_ttl_secs)
        await store.refresh_owner(binding_id, self._owner_lease_secs)
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
        updates: list[asyncio.Task[bool]] = []
        heartbeat = asyncio.create_task(
            self._owner_heartbeat(record.id, store),
        )

        async def persist_qr(snapshot: ChannelCredentialBindingRecord) -> bool:
            expire_in = max(
                int(
                    (
                        datetime.fromisoformat(snapshot.expires_at)
                        - datetime.now()
                    ).total_seconds(),
                ),
                1,
            )
            saved = await store.replace(
                snapshot,
                expire_in + self._terminal_ttl_secs,
                {
                    ChannelCredentialBindingState.PENDING,
                    ChannelCredentialBindingState.SCANNED,
                },
            )
            if not qr_ready.done():
                if saved:
                    qr_ready.set_result(None)
                else:
                    qr_ready.set_exception(
                        ChannelError(
                            "Credential binding session was cancelled.",
                            409,
                        ),
                    )
            return saved

        def on_qr_code(info: dict) -> None:
            expire_in = max(int(info.get("expire_in", 300)), 1)
            record.expires_at = (
                datetime.now() + timedelta(seconds=expire_in)
            ).isoformat()
            try:
                record.qr_code_url = _verification_url_to_qr_data_url(
                    str(info["url"]),
                )
            except Exception as exc:  # pylint: disable=broad-except
                if not qr_ready.done():
                    qr_ready.set_exception(exc)
                return
            record.message = "Waiting for authorization in Feishu or Lark."
            updates.append(
                asyncio.create_task(persist_qr(record.model_copy(deep=True))),
            )

        def on_status_change(info: dict) -> None:
            status = str(info.get("status", ""))
            if status == "slow_down":
                record.message = "Authorization is still pending."
            elif status == "domain_switched":
                record.message = "Continue authorization in Lark."
            else:
                return
            updates.append(
                asyncio.create_task(
                    store.replace(
                        record.model_copy(deep=True),
                        max(
                            int(
                                (
                                    datetime.fromisoformat(record.expires_at)
                                    - datetime.now()
                                ).total_seconds(),
                            ),
                            1,
                        )
                        + self._terminal_ttl_secs,
                        {
                            ChannelCredentialBindingState.PENDING,
                            ChannelCredentialBindingState.SCANNED,
                        },
                    ),
                ),
            )

        try:
            result: dict[str, Any] = await sdk.aregister_app(
                on_qr_code=on_qr_code,
                on_status_change=on_status_change,
                source="agentscope",
                app_preset=self._app_preset,
                addons=self._addons,
            )
            if updates:
                await asyncio.gather(*updates)
            record.credentials = {
                "app_id": str(result["client_id"]),
                "app_secret": str(result["client_secret"]),
            }
            record.state = ChannelCredentialBindingState.AUTHORIZED
            record.expires_at = (
                datetime.now() + timedelta(seconds=self._authorized_ttl_secs)
            ).isoformat()
            record.message = "Feishu application created successfully."
            await store.replace(
                record,
                self._authorized_ttl_secs,
                {
                    ChannelCredentialBindingState.PENDING,
                    ChannelCredentialBindingState.SCANNED,
                },
            )
        except asyncio.CancelledError:
            if not qr_ready.done():
                qr_ready.cancel()
            raise
        except Exception as exc:  # pylint: disable=broad-except
            if updates:
                await asyncio.gather(*updates, return_exceptions=True)
            name = type(exc).__name__
            if name == "AppExpiredError":
                record.state = ChannelCredentialBindingState.EXPIRED
            else:
                record.state = ChannelCredentialBindingState.FAILED
            record.credentials = None
            record.expires_at = (
                datetime.now() + timedelta(seconds=self._terminal_ttl_secs)
            ).isoformat()
            record.message = str(exc) or "Feishu authorization failed."
            await store.replace(
                record,
                self._terminal_ttl_secs,
                {
                    ChannelCredentialBindingState.PENDING,
                    ChannelCredentialBindingState.SCANNED,
                },
            )
            if not qr_ready.done():
                qr_ready.set_exception(
                    ChannelError(record.message, 502),
                )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await store.clear_owner(record.id)

    async def _owner_heartbeat(
        self,
        binding_id: str,
        store: ChannelCredentialBindingStore,
    ) -> None:
        """Keep a short shared lease while this worker drives polling."""
        interval = max(self._owner_lease_secs / 3, 0.25)
        while True:
            await asyncio.sleep(interval)
            if not await store.refresh_owner(
                binding_id,
                self._owner_lease_secs,
            ):
                return

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
        """Return status, including a retryable lost-worker failure."""
        record = await self._owned(user_id, binding_id, store)
        active_states = {
            ChannelCredentialBindingState.PENDING,
            ChannelCredentialBindingState.SCANNED,
        }
        if record.state in active_states:
            failed = record.model_copy(
                update={
                    "state": ChannelCredentialBindingState.FAILED,
                    "credentials": None,
                    "message": (
                        "The authorization worker stopped. "
                        "Please retry QR binding."
                    ),
                    "expires_at": (
                        datetime.now()
                        + timedelta(seconds=self._terminal_ttl_secs)
                    ).isoformat(),
                },
            )
            current, failed_owner = await store.replace_if_owner_missing(
                failed,
                self._terminal_ttl_secs,
                active_states,
            )
            if current is None:
                record = await self._owned(user_id, binding_id, store)
            else:
                record = current
            if failed_owner:
                task = self._tasks.get(binding_id)
                if task is not None:
                    task.cancel()
        if record.state in active_states | {
            ChannelCredentialBindingState.AUTHORIZED,
        } and datetime.now() >= datetime.fromisoformat(record.expires_at):
            record.state = ChannelCredentialBindingState.EXPIRED
            record.credentials = None
            record.message = "The Feishu credential binding expired."
            record.expires_at = (
                datetime.now() + timedelta(seconds=self._terminal_ttl_secs)
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
        if datetime.now() >= datetime.fromisoformat(record.expires_at):
            record.state = ChannelCredentialBindingState.EXPIRED
            record.credentials = None
            record.message = "The Feishu credential binding expired."
            record.expires_at = (
                datetime.now() + timedelta(seconds=self._terminal_ttl_secs)
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
