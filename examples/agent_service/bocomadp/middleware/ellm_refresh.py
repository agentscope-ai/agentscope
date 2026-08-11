# -*- coding: utf-8 -*-
"""ELLM key-refresh agent middleware.

每次模型调用前，通过 :class:`EllmKeyRefresher` 惰性检查/刷新 ELLM
apikey（过期判定 + ``MessageBus.acquire_lock`` 并发防抖 + 失败回落），
再用 ``EllmChatModel.set_api_key`` 把新鲜 key 注入到当前模型实例的
请求头（``Authorization: Bearer <key>``），并从凭证记录同步
``inject_think_tag`` 开关——不换类、不重建 client，模型调用链保持不变。

挂载方式（bocomadp main.py）::

    from bocomadp.middleware.ellm_refresh import build_ellm_refresh_middleware

    app = create_app(
        ...,
        extra_agent_middlewares=build_ellm_refresh_middleware(storage, message_bus),
    )
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

try:
    from agentscope.middleware import MiddlewareBase
except ImportError:  # pragma: no cover — offline syntax fallback
    class MiddlewareBase:  # type: ignore
        """Fallback MiddlewareBase for syntax checking."""

        _is_agent_middleware = True

        def is_implemented(self, hook_name: str) -> bool:
            base = getattr(MiddlewareBase, hook_name, None)
            sub = getattr(type(self), hook_name, None)
            return base is not sub

        async def list_tools(self) -> list:
            return []

        async def get_middleware_key(self) -> str:
            return self.__class__.__name__


MiddlewareBase._is_agent_middleware = True  # type: ignore[attr-defined]

from bocomadp.providers.ellm_chat_model import EllmChatModel  # noqa: E402


class EllmKeyRefreshMiddleware(MiddlewareBase):
    """每次模型调用前把刷新后的 ELLM apikey 注入模型请求头。

    - 非 :class:`EllmChatModel` 模型直接透传，不做任何处理；
    - :class:`EllmChatModel` 模型：``EllmKeyRefresher`` 惰性刷新 →
      ``set_api_key`` 注入 → 同步 ``inject_think_tag`` 开关。
    """

    def __init__(self, storage: Any, message_bus: Any, user_id: str) -> None:
        from bocomadp.providers.ellm_key import EllmKeyRefresher

        self._refresher = EllmKeyRefresher(storage, message_bus, user_id)

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Any,
    ) -> Any:
        current_model = input_kwargs.get("current_model")

        if isinstance(current_model, EllmChatModel):
            credential_id = getattr(
                getattr(current_model, "credential", None),
                "id",
                None,
            )
            if credential_id:
                key, record = await self._refresher.ensure_fresh_key(
                    credential_id,
                )
                current_model.set_api_key(key)
                current_model.inject_think_tag = bool(
                    record.data.get("inject_think_tag", False),
                )
                logger.debug(
                    "injected refreshed ELLM key (user=%s, credential=%s)",
                    self._refresher.user_id,
                    credential_id,
                )

        return await next_handler(**input_kwargs)


def build_ellm_refresh_middleware(
    storage: Any,
    message_bus: Any,
) -> Any:
    """构造 ``AgentMiddlewareFactory``，供 ``create_app(extra_agent_middlewares=...)`` 使用。

    Args:
        storage: StorageBase 实例（bocomadp main.py 已持有）。
        message_bus: MessageBus 实例。

    Returns:
        ``AgentMiddlewareFactory`` —— ``async (user_id, agent_id, session_id)
        -> list[MiddlewareBase]``。
    """

    async def factory(
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> list:
        return [EllmKeyRefreshMiddleware(storage, message_bus, user_id)]

    return factory


__all__ = ["EllmKeyRefreshMiddleware", "build_ellm_refresh_middleware"]
