"""Private, interactive authentication for a tool running in a chat."""

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .._logging import logger
from ..event import CustomEvent
from ._bus_ops import publish_session_event
from .message_bus import MessageBus

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


@dataclass
class _Pending:
    view: LoginView
    complete: CompleteStep = field(repr=False)
    result: asyncio.Future[LoginSuccess | LoginFailed | None] = field(repr=False)
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
        created = pending is None
        if pending is None:
            pending = _Pending(
                LoginView(id=uuid4().hex, revision=0, step=step),
                complete,
                asyncio.get_running_loop().create_future(),
            )
            self._pending[key] = pending

        pending.waiters += 1
        try:
            async with asyncio.timeout(self._timeout):
                if created:
                    await self._notify(scope)
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
            if pending.waiters == 0 and self._finish(key, pending, None):
                await self._notify(scope)

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
                result = await pending.complete(step, values)
            except LoginFailed as exc:
                if self._finish(key, pending, LoginFailed(str(exc))):
                    await self._notify(scope)
                raise
            if self._pending.get(key) is not pending:
                raise LoginEnded("This login request has ended.")
            if isinstance(result, LoginSuccess):
                self._finish(key, pending, result)
            else:
                pending.view = LoginView(
                    id=login_id, revision=revision + 1, step=result,
                )
        finally:
            pending.submitting = False
        await self._notify(scope)

    async def cancel(self, scope: LoginScope, login_id: str) -> None:
        """Cancel the operation without delivering a credential to the tool."""
        service, pending = self._find(scope, login_id)
        self._finish((scope, service), pending, None)
        await self._notify(scope)
