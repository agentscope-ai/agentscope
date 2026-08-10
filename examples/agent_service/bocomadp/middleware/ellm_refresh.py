# -*- coding: utf-8 -*-
"""ELLM key-refresh agent middleware.

替换每次模型调用前的 ``current_model``：当模型是
:class:`agentscope.model.EllmChatModel` 时，包装为
:class:`bocomadp.providers.auto_refresh_ellm_model.AutoRefreshEllmChatModel`，
使其在每次 ``_call_api`` 前惰性检查 apikey 过期并刷新（经
``MessageBus.acquire_lock`` 并发防抖），再以 ``extra_headers``
（``Authorization: Bearer <key>``）注入本次请求。

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


class EllmKeyRefreshMiddleware(MiddlewareBase):
    """将 ELLM 基类模型替换为 AutoRefresh 模型（仅当模型是 EllmChatModel）。"""

    def __init__(self, storage: Any, message_bus: Any, user_id: str) -> None:
        self._storage = storage
        self._message_bus = message_bus
        self._user_id = user_id

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Any,
    ) -> Any:
        current_model = input_kwargs.get("current_model")
        refreshed = await self._build_refreshed_model(current_model)
        return await next_handler(
            **{**input_kwargs, "current_model": refreshed},
        )

    async def _build_refreshed_model(self, model: Any) -> Any:
        from agentscope.model import EllmChatModel

        if not isinstance(model, EllmChatModel):
            return model

        from bocomadp.providers.auto_refresh_ellm_model import (
            AutoRefreshEllmChatModel,
        )

        if isinstance(model, AutoRefreshEllmChatModel):
            return model

        logger.info(
            "wrapping EllmChatModel with AutoRefreshEllmChatModel "
            "(user=%s, credential=%s)",
            self._user_id,
            getattr(getattr(model, "credential", None), "id", "?"),
        )
        refreshed = AutoRefreshEllmChatModel(
            storage=self._storage,
            message_bus=self._message_bus,
            user_id=self._user_id,
            credential_id=model.credential.id,
            credential=model.credential,
            model=model.model,
            parameters=model.parameters,
            stream=model.stream,
        )
        # 复用 base 模型的 openai client（其 base_url 指向 ELLM 网关），
        # 避免 AutoRefresh 构造时新建一个指向同一网关的 client。
        if getattr(model, "client", None) is not None:
            refreshed.client = model.client
        return refreshed


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
