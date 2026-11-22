"""Private, interactive authentication for a tool running in a chat."""

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import aclosing, asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .._logging import logger
from ..event import CustomEvent
from ._bus_ops import publish_session_event
from .message_bus import MessageBus, MessageBusKeys

LoginScope = tuple[str, str, str]
_scope: ContextVar[LoginScope | None] = ContextVar("tool_login_scope", default=None)


@contextmanager
def tool_login_scope(user_id: str, agent_id: str, session_id: str) -> Iterator[None]:
    """Bind a chat run and its tools to the current session."""
    token = _scope.set((user_id, agent_id, session_id))
    try:
        yield
    finally:
        _scope.reset(token)


class LoginField(BaseModel):
    """An input declared by the service adapter."""

    name: str = Field(min_length=1, max_length=80)
    label: str
    kind: Literal["text", "secret"] = "text"
    required: bool = True


class LoginStep(BaseModel):
    """Public instructions and inputs for the current step."""

    title: str
    description: str
    submit_label: str
    fields: list[LoginField] = Field(max_length=12)


class LoginView(BaseModel):
    """The form currently waiting for input."""

    id: str
    revision: int
    step: LoginStep


@dataclass(frozen=True, repr=False)
class LoginSuccess:
    """An opaque authenticated value returned exclusively to the tool."""

    value: Any


class LoginRejected(Exception):
    """A retryable, user-safe message supplied by the adapter."""


class LoginEnded(Exception):
    """The authentication request was cancelled, expired, or unavailable."""


class LoginFailed(Exception):
    """A permanent adapter error that should return control to the tool."""


class LoginConflict(Exception):
    """The submitted step is stale or already in progress."""


CompleteStep = Callable[
    [LoginStep, Mapping[str, str]],
    Awaitable[LoginStep | LoginSuccess],
]


@asynccontextmanager
async def _subscribed(bus, channel, *, until=None):
    """Subscribe before advertising a form or sending a command needing a reply."""
    ready = asyncio.Event()
    async with aclosing(bus.subscribe(channel, on_ready=ready.set)) as stream:
        first = asyncio.create_task(anext(stream))
        started = asyncio.create_task(ready.wait())
        try:
            waiting = (first, started) if until is None else (first, started, until)
            await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            if until is not None and until.done():
                raise LoginEnded("This login request has ended.")
            if first.done():
                first.result()  # surface a failed subscription
            yield first, stream
        finally:
            for task in (first, started):
                task.cancel()
            await asyncio.gather(first, started, return_exceptions=True)


def _directory(scope):
    digest = hashlib.sha256(json.dumps(scope).encode()).hexdigest()
    return MessageBusKeys.tool_login_forms(digest)


_RELAY_ERRORS = {cls.__name__: cls for cls in (
    LoginConflict, LoginEnded, LoginFailed, LoginRejected,
)}


@dataclass
class _Pending:
    view: LoginView
    complete: CompleteStep = field(repr=False)
    result: asyncio.Future[LoginSuccess | LoginFailed | None] = field(repr=False)
    created: float = field(default_factory=time.time)
    listener: asyncio.Task | None = field(default=None, repr=False)
    waiters: int = 0
    submitting: bool = False


class ToolLogin:
    """Coordinate login requests in the process that owns the tool call."""

    def __init__(self, message_bus: MessageBus, timeout: float = 180) -> None:
        self._bus = message_bus
        self._timeout = timeout
        self._pending: dict[tuple[LoginScope, str], _Pending] = {}

    @staticmethod
    def current_scope() -> LoginScope:
        """Return the active chat scope, for example to key an adapter cache."""
        scope = _scope.get()
        if scope is None:
            raise LoginEnded("Tool login requires an active chat session.")
        return scope

    async def _notify(self, scope: LoginScope) -> None:
        # Events carry only an invalidation. Forms are read from the scoped API.
        try:
            await publish_session_event(
                self._bus,
                scope[2],
                CustomEvent(name="tool_login_changed", value={}).model_dump(mode="json"),
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Could not notify chat of a tool login change")

    async def request(
        self,
        service: str,
        step: LoginStep,
        complete: CompleteStep,
    ) -> Any:
        """Suspend the calling tool until the adapter accepts user input."""
        scope = self.current_scope()
        key = (scope, service)
        pending = self._pending.get(key)
        if pending is None:
            pending = _Pending(
                LoginView(id=uuid4().hex, revision=0, step=step),
                complete,
                asyncio.get_running_loop().create_future(),
            )
            self._pending[key] = pending
            pending.listener = asyncio.create_task(self._serve(key, pending))

        pending.waiters += 1
        try:
            async with asyncio.timeout(self._timeout):
                result = await asyncio.shield(pending.result)
            if result is None:
                raise LoginEnded("Login cancelled. Stop the current operation.")
            if isinstance(result, LoginFailed):
                raise LoginFailed(str(result))
            return result.value
        except TimeoutError:
            if self._finish(key, pending, None):
                await self._notify(scope)
            raise LoginEnded("Login timed out. Stop the current operation.") from None
        finally:
            pending.waiters -= 1
            if pending.waiters == 0:
                self._finish(key, pending, None)
                await asyncio.shield(pending.listener)

    async def _advertise(self, scope, pending):
        # Only public form metadata is stored. Passwords and replies use private,
        # transient channels and are never written to the chat replay log.
        value = {
            "view": pending.view.model_dump(mode="json"),
            "created": pending.created,
            "expires": pending.created + self._timeout,
        }
        await self._bus.registry_set(
            _directory(scope), pending.view.id, json.dumps(value),
            ttl_secs=max(1, math.ceil(self._timeout)),
        )
        await self._notify(scope)

    async def _withdraw(self, scope, login_id):
        try:
            await self._bus.registry_del(_directory(scope), login_id)
        except Exception:
            # A lost connection must not replace an adapter result. Orphaned
            # public form records also carry the original request deadline.
            logger.warning("Could not remove expired tool login form")

    async def _serve(self, key, pending):
        scope, _ = key
        login_id = pending.view.id
        channel = MessageBusKeys.tool_login_command(login_id)
        try:
            async with _subscribed(self._bus, channel, until=pending.result) as (incoming, stream):
                if pending.result.done():
                    return
                await self._advertise(scope, pending)
                try:
                    async with asyncio.TaskGroup() as handlers:
                        while not pending.result.done():
                            await asyncio.wait((incoming, pending.result), return_when=asyncio.FIRST_COMPLETED)
                            if incoming.done():
                                command = incoming.result()
                                handlers.create_task(self._handle_command(scope, pending, command))
                                incoming = asyncio.create_task(anext(stream))
                finally:
                    incoming.cancel()
                    await asyncio.gather(incoming, return_exceptions=True)
        except Exception:
            self._finish(key, pending, LoginFailed("Tool login transport unavailable."))
        finally:
            await self._withdraw(scope, login_id)
            await self._notify(scope)

    async def _handle_command(self, scope, pending, command):
        # Keep listening during a slow adapter call so another tab can cancel
        # or receive an immediate revision conflict.
        reply = {}
        login_id = pending.view.id
        try:
            async with asyncio.timeout(max(0, pending.created + self._timeout - time.time())):
                if command["action"] == "cancel":
                    await self._cancel_local(scope, login_id)
                else:
                    await self._submit_local(scope, login_id, command["revision"], command["values"])
        except (LoginConflict, LoginEnded, LoginFailed, LoginRejected) as error:
            kind = next(name for name, cls in _RELAY_ERRORS.items() if isinstance(error, cls))
            reply = {"error": kind, "message": str(error)}
        except Exception:
            reply = {"error": "LoginFailed", "message": "The service could not complete login."}
        await self._bus.publish(MessageBusKeys.tool_login_reply(command["request"]), reply)

    async def pending_view(self, scope: LoginScope) -> LoginView | None:
        """Find the oldest active form, including those owned by another worker."""
        records = (await self._bus.registry_getall(_directory(scope))).values()
        live = (r for r in map(json.loads, records) if r["expires"] > time.time())
        oldest = min(live, key=lambda r: (r["created"], r["view"]["id"]), default=None)
        return LoginView.model_validate(oldest["view"]) if oldest else None

    async def _forward(self, scope, login_id, action, **payload):
        record = await self._bus.registry_get(_directory(scope), login_id)
        remaining = json.loads(record)["expires"] - time.time() if record else 0
        if remaining <= 0:
            raise LoginEnded("This login request has ended.")
        request_id = uuid4().hex
        try:
            async with asyncio.timeout(remaining):
                async with _subscribed(self._bus, MessageBusKeys.tool_login_reply(request_id)) as (reply, _):
                    await self._bus.publish(MessageBusKeys.tool_login_command(login_id), {
                        "action": action, "request": request_id, **payload,
                    })
                    result = await reply
        except (TimeoutError, StopAsyncIteration):
            raise LoginEnded("The login worker did not respond in time.") from None
        if error := result.get("error"):
            raise _RELAY_ERRORS[error](result["message"])

    def _finish(
        self,
        key: tuple[LoginScope, str],
        pending: _Pending,
        result: LoginSuccess | LoginFailed | None,
    ) -> bool:
        if self._pending.get(key) is not pending:
            return False
        del self._pending[key]
        pending.result.set_result(result)
        return True

    def pending(self, scope: LoginScope) -> LoginView | None:
        """Return the oldest visible login request for a chat."""
        return next(
            (entry.view for (owner, _), entry in self._pending.items() if owner == scope),
            None,
        )

    def _find(self, scope: LoginScope, login_id: str) -> tuple[str, _Pending]:
        for (owner, service), entry in self._pending.items():
            if owner == scope and entry.view.id == login_id:
                return service, entry
        raise LoginEnded("This login request has ended.")

    async def submit(
        self,
        scope: LoginScope,
        login_id: str,
        revision: int,
        values: dict[str, str],
    ) -> None:
        """Submit only the fields declared by the current adapter step."""
        try:
            self._find(scope, login_id)
        except LoginEnded:
            return await self._forward(scope, login_id, "submit", revision=revision, values=values)
        await self._submit_local(scope, login_id, revision, values)

    async def _submit_local(self, scope, login_id, revision, values):
        service, pending = self._find(scope, login_id)
        if pending.submitting or pending.view.revision != revision:
            raise LoginConflict("This login step has changed or is being submitted.")
        step = pending.view.step
        fields = {item.name for item in step.fields}
        if values.keys() - fields or any(
            item.required and not values.get(item.name) for item in step.fields
        ):
            raise LoginRejected("Complete the fields in the current login form.")

        pending.submitting = True
        key = (scope, service)
        try:
            try:
                completion = asyncio.ensure_future(pending.complete(step, values))
                try:
                    await asyncio.wait((completion, pending.result), return_when=asyncio.FIRST_COMPLETED)
                    if pending.result.done():
                        raise LoginEnded("This login request has ended.")
                    result = completion.result()
                finally:
                    completion.cancel()
                    await asyncio.gather(completion, return_exceptions=True)
            except LoginFailed as exc:
                self._finish(key, pending, LoginFailed(str(exc)))
                raise
            if self._pending.get(key) is not pending:
                raise LoginEnded("This login request has ended.")
            if isinstance(result, LoginSuccess):
                self._finish(key, pending, result)
                await self._withdraw(scope, login_id)
            else:
                pending.view = LoginView(
                    id=login_id, revision=revision + 1, step=result,
                )
                await self._advertise(scope, pending)
        finally:
            pending.submitting = False

    async def cancel(self, scope: LoginScope, login_id: str) -> None:
        """Cancel the operation without delivering a credential to the tool."""
        try:
            self._find(scope, login_id)
        except LoginEnded:
            return await self._forward(scope, login_id, "cancel")
        await self._cancel_local(scope, login_id)

    async def _cancel_local(self, scope, login_id):
        service, pending = self._find(scope, login_id)
        self._finish((scope, service), pending, None)
        await self._withdraw(scope, login_id)
