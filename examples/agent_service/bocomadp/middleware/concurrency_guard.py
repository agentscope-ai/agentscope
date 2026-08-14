# -*- coding: utf-8 -*-
"""/chat 并发控制 ASGI 中间件(入口对账 + 占位 + 响应回滚/注册)。

请求方向:
- 首次请求执行一次 ``reconcile_on_startup``(重建计数,吸收漂移;
  框架 create_app 使用自定义 lifespan,@app.on_event 不执行,故惰性到
  首个请求时执行,此时事件循环已运行);
- 每次请求先 ``reconcile(grace_secs)``(入口对账,无限频)再 ``try_acquire``;
响应方向:
- 2xx → 从响应体解析 session_id 并注册(值带 user:ts:token);
- 非 2xx → 回滚名额。
任何 Redis 异常 → fail-open 放行(记日志),绝不阻断正常请求。
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

logger = logging.getLogger("bocomadp.concurrency.guard")

Scope = dict
Message = dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_CHAT_PATH = "/chat/"
_THROTTLED_BODY = b'{"status": "throttled", "session_id": ""}'


class ConcurrencyGuardMiddleware:
    """Gate POST /chat/ with global + per-user concurrency limits."""

    def __init__(
        self,
        app: ASGIApp,
        guard,
        *,
        enabled: bool = True,
        grace_secs: float = 6.0,
    ) -> None:
        self.app = app
        self._guard = guard
        self._enabled = enabled
        self._grace_secs = grace_secs
        self._startup_reconciled = False

    async def _reconcile_on_startup(self) -> None:
        """启动对账惰性执行;成功才置位,失败保留重试机会。"""
        if not self._startup_reconciled:
            try:
                await self._guard.reconcile_on_startup()
            except Exception:  # noqa: BLE001 — fail-open:放行请求,下次请求重试
                logger.exception("concurrency guard startup reconcile failed; fail-open (will retry)")
                return
            self._startup_reconciled = True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            not self._enabled
            or scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != _CHAT_PATH
        ):
            await self.app(scope, receive, send)
            return

        await self._reconcile_on_startup()

        user_id = ""
        for name, value in scope.get("headers", []):
            if name == b"x-user-id":
                user_id = value.decode("utf-8", "replace")
                break
        if not user_id:
            # 与框架 get_current_user_id 一致:缺失时框架会 401,中间件不拦
            await self.app(scope, receive, send)
            return

        try:
            await self._guard.reconcile(self._grace_secs)
            acquired = await self._guard.try_acquire(user_id)
        except Exception:  # noqa: BLE001 — fail-open
            logger.exception("concurrency guard reconcile/acquire failed; fail-open")
            await self.app(scope, receive, send)
            return

        if not acquired:
            headers = [(b"content-type", b"application/json")]
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            await send({"type": "http.response.body", "body": _THROTTLED_BODY, "more_body": False})
            return

        await self._pass_through_and_finalize(scope, receive, send, user_id)

    async def _pass_through_and_finalize(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        user_id: str,
    ) -> None:
        """转发下游,缓冲 JSON 响应体,结束时注册或回滚。"""
        status = 200
        content_type = ""
        body_parts: list[bytes] = []
        buffer = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status, content_type, buffer
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = dict(message.get("headers", []))
                content_type = headers.get(b"content-type", b"").decode("latin-1", "replace")
                buffer = content_type.startswith("application/json")
                await send(message)
            elif message["type"] == "http.response.body":
                if buffer:
                    body_parts.append(message.get("body", b""))
                    if not message.get("more_body", False):
                        await self._finalize(status, body_parts, user_id)
                await send(message)
            else:
                await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:  # noqa: BLE001 — fail-open:回滚后上抛,由外层中间件统一处理
            logger.exception("concurrency guard downstream failed; rolling back")
            await self._rollback(user_id)
            raise

    async def _finalize(self, status: int, body_parts: list[bytes], user_id: str) -> None:
        body = b"".join(body_parts)
        session_id = ""
        try:
            payload = json.loads(body)
            session_id = str(payload.get("session_id", ""))
        except Exception:  # noqa: BLE001
            session_id = ""
        if status >= 200 and status < 300:
            if session_id:
                try:
                    await self._guard.register(session_id, user_id)
                    return
                except Exception:  # noqa: BLE001 — 注册失败必须回滚,避免计数泄漏
                    logger.exception("concurrency guard register failed; rolling back")
            await self._rollback(user_id)
        else:
            await self._rollback(user_id)

    async def _rollback(self, user_id: str) -> None:
        # 回滚不需要 session_id:HDEL 不存在的 field 返回 0,无害
        try:
            await self._guard.rollback("", user_id)
        except Exception:  # noqa: BLE001 — fail-open
            logger.exception("concurrency guard rollback failed")
