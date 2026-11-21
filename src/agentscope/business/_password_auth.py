"""Declarative JSON password authentication for host-bound business APIs."""

from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..app.tool_login import (
    LoginFailed, LoginField, LoginRejected, LoginStep, LoginSuccess, ToolLogin,
)


def at_path(payload: object, path: str) -> Any:
    """Read a dotted path in a JSON object."""
    for segment in path.split("."):
        if not isinstance(payload, dict):
            return None
        payload = payload.get(segment)
    return payload


def json_object(response: httpx.Response) -> dict:
    """Read optional JSON while preserving the HTTP status for classification."""
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


class ResponseMatch(BaseModel):
    """Conjunction of status and exact JSON conditions supplied by the agent."""

    model_config = ConfigDict(extra="forbid")

    status: int | None = Field(default=None, ge=100, le=599)
    fields: dict[str, str | int | bool] = Field(default_factory=dict)

    def matches(self, response: httpx.Response) -> bool:
        """A match needs at least one explicit condition."""
        if self.status is not None and response.status_code != self.status:
            return False
        payload = json_object(response)
        return (self.status is not None or bool(self.fields)) and all(
            (actual := at_path(payload, key)) == value and type(actual) is type(value)
            for key, value in self.fields.items()
        )


class SourceEvidence(BaseModel):
    """A source line range supporting a rule."""

    path: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class PasswordProfile(BaseModel):
    """A model-generated profile for a login endpoint and protected read."""

    model_config = ConfigDict(extra="forbid")

    login_path: str = Field(pattern=r"^(?:/[A-Za-z0-9_-]+)+$")
    request_fields: dict[str, str]
    accepted: ResponseMatch
    rejected: list[ResponseMatch] = Field(min_length=1)
    expired: list[ResponseMatch] = Field(min_length=1)
    blocked_login: dict[str, str] = Field(default_factory=dict)
    token_path: str = Field(
        pattern=r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$",
        description="Dotted JSON path to the token in the login response.",
    )
    session_path: str = Field(
        pattern=r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$",
        description="Dotted JSON path to the session object in the same login response.",
    )
    client_route: str
    query_path: str = Field(pattern=r"^(?:/[A-Za-z0-9_-]+)+$")
    evidence: list[SourceEvidence] = Field(min_length=3)

    def credentials_rejected(self, response: httpx.Response) -> bool:
        """Decide whether the login response invites another attempt."""
        return any(rule.matches(response) for rule in self.rejected)

    def session_expired(self, response: httpx.Response) -> bool:
        """Classify a protected endpoint response using this service's rules."""
        return any(rule.matches(response) for rule in self.expired)


class PasswordRejected(LoginRejected):
    """The upstream service explicitly rejected the supplied password."""


class PasswordConnection:
    """Execute one approved profile on a host-selected upstream service."""

    def __init__(
        self,
        *,
        broker: ToolLogin,
        service: str,
        title: str,
        base_url: str,
        context: Mapping[str, str | int],
        previous_password: Callable[[], str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.broker = broker
        self.service = service
        self.title = title
        self.base_url = base_url.rstrip("/") + "/"
        self.context = dict(context)
        self.previous_password = previous_password
        self.transport = transport
        self._tokens: OrderedDict[tuple, dict] = OrderedDict()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, timeout=15, trust_env=False,
            follow_redirects=False, transport=self.transport,
        )

    async def _login(self, profile: PasswordProfile, password: str) -> dict:
        values = {**self.context, "password": password}
        request = {name: values[role] for name, role in profile.request_fields.items()}
        async with self._client() as client:
            response = await client.post(profile.login_path.lstrip("/"), json=request)
        if profile.credentials_rejected(response):
            raise PasswordRejected("密码错误，请重试。")
        if not profile.accepted.matches(response):
            raise LoginRejected("服务未接受此次登录，请检查账号状态后重试。")
        payload = json_object(response)
        for path, notice in profile.blocked_login.items():
            if at_path(payload, path) is True:
                raise LoginRejected(notice)
        token = at_path(payload, profile.token_path)
        session = at_path(payload, profile.session_path)
        if not isinstance(token, str) or not token or not isinstance(session, dict):
            raise LoginFailed("登录响应与 token_path 或 session_path 不一致，请核对登录响应结构。")
        return {"token": token, "session": session}

    async def connect(
        self,
        profile: PasswordProfile,
        *,
        expired_token: str | None = None,
        on_bad_password: Callable[[], None] | None = None,
    ) -> dict:
        """Reuse a chat's session or suspend until the user has signed in."""
        key = (*self.broker.current_scope(), profile.model_dump_json())
        saved = self._tokens.get(key)
        if saved and saved["token"] != expired_token:
            self._tokens.move_to_end(key)
            return saved
        self._tokens.pop(key, None)
        try:
            auth = await self._login(profile, self.previous_password())
            if auth["token"] == expired_token:
                raise ValueError("服务重复返回已失效的会话令牌。")
        except PasswordRejected:
            if on_bad_password:
                on_bad_password()

            async def complete(_step: LoginStep, values: Mapping[str, str]) -> LoginSuccess:
                try:
                    auth = await self._login(profile, values["password"])
                except PasswordRejected:
                    if on_bad_password:
                        on_bad_password()
                    raise
                return LoginSuccess(auth)

            auth = await self.broker.request(
                self.service,
                LoginStep(
                    title=f"{self.title} 需要重新登录",
                    description="请输入当前密码，登录成功后会继续刚才的操作。",
                    submit_label="登录并继续",
                    fields=[LoginField(name="password", label="密码", kind="secret")],
                ),
                complete,
            )
        self._tokens[key] = auth
        self._tokens.move_to_end(key)
        if len(self._tokens) > 256:
            self._tokens.popitem(last=False)
        return auth

    async def read(
        self,
        profile: PasswordProfile,
        params: Mapping[str, Any],
        *,
        on_bad_password: Callable[[], None] | None = None,
        prepare: Callable[[dict], Awaitable[Mapping[str, Any]]] | None = None,
    ) -> httpx.Response:
        """Send a GET, retrying once after an adapter-recognized session loss."""
        auth = await self.connect(profile, on_bad_password=on_bad_password)
        if prepare is not None:
            params = await prepare(auth)
        async with self._client() as client:
            response = await client.get(
                profile.query_path.lstrip("/"), params=params,
                headers={"Authorization": f"Bearer {auth['token']}"},
            )
            if profile.session_expired(response):
                auth = await self.connect(
                    profile, expired_token=auth["token"],
                    on_bad_password=on_bad_password,
                )
                response = await client.get(
                    profile.query_path.lstrip("/"), params=params,
                    headers={"Authorization": f"Bearer {auth['token']}"},
                )
        return response
