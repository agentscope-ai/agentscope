# -*- coding: utf-8 -*-
"""外部 skillhub 提供者（迁移自 ``agentscope.app.hub._skill._external_hub``）。

对外部 skillhub HTTP API 的薄异步客户端，通过
:class:`~agentscope.app.hub._skill._base.SkillHubBase` 接口暴露目录与
下载能力，使 Web UI 与 workspace 流程将其视为普通 skill hub。

认证为 cookie 式、token 驱动：调用方通过 :meth:`set_token` 每次请求
传入 ``guwpToken``；每次调用都会向登录端点换取新的 ``SESSION``
cookie（无缓存）。

服务地址从 :mod:`bankcomm_adp.config` 读取（``ADP_EXTERNAL_SKILLHUB_URL``）。
"""
from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, AsyncIterator

from agentscope._logging import logger
from agentscope.app.hub._error import HubError
from agentscope.app.hub._skill._base import SkillArchive, SkillHubBase

from ._card import SkillCard, SkillHubPage

from ..config import settings

if TYPE_CHECKING:
    import httpx

#: 目录查询端点路径（命名空间走 query 参数）。
CATALOG_PATH = "/api/web/skills"

#: 下载端点前缀 —— 最终 URL 为 ``{base_url}{DOWNLOAD_PREFIX}/{card_id}/download``。
DOWNLOAD_PREFIX = "/api/web/skills/global"

#: 目录命名空间。
CATALOG_NAMESPACE = "global"

#: 当前用户已上传 skill 的端点路径。
MY_SKILLS_PATH = "/api/web/me/skills"

#: 默认流式块大小（64 KiB）。
DEFAULT_CHUNK_SIZE = 64 * 1024


class ExternalSkillHub(SkillHubBase):
    """基于部署方自有 skillhub 服务器的 skill hub。

    .. code-block:: python

        hub = ExternalSkillHub()                 # base_url 取 settings
        hub.set_token("guwp_...")                # 可选，按请求设置
        page = await hub.list_skills(user_id="alice", q="write")
        archive = await hub.download("alice", "write")
    """

    def __init__(
        self,
        hub_id: str = "external",
        display_name: str = "External SkillHub",
        description: str = "外部 skillhub 目录",
        icon_url: str | None = None,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        """初始化外部 skillhub 提供者。

        Args:
            hub_id (`str`): 路由中寻址该 hub 的稳定 id。
            display_name (`str`): 用户可见名称。
            description (`str`): 用户可见描述。
            icon_url (`str | None`): hub 图标。
            base_url (`str | None`): skillhub 服务地址；``None`` 时
                取 ``settings.external_skillhub_url``。
            api_token (`str | None`): 初始 ``guwpToken``，可后续通过
                :meth:`set_token` 更新。
            timeout (`float`): 单请求超时（秒）。
        """
        super().__init__(hub_id, display_name, description, icon_url)
        self.base_url = (base_url or settings.external_skillhub_url).rstrip("/")
        self.timeout = timeout
        self._guwp_token = api_token
        self._client: "httpx.AsyncClient | None" = None

    def set_token(self, token: str | None) -> None:
        """更新用于 cookie 刷新的 ``guwpToken``。

        可逐请求调用——下一次调用会用新 token 重新认证。
        """
        self._guwp_token = token

    # ── 生命周期 ────────────────────────────────────────────────

    async def __aenter__(self) -> "ExternalSkillHub":
        """打开共享 HTTP 客户端。"""
        import httpx

        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """关闭共享 HTTP 客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> "httpx.AsyncClient":
        """返回共享客户端；未进入上下文时按需创建。"""
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _headers(self, cookie: str) -> dict[str, str]:
        """构造请求头（含会话 cookie）。"""
        return {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Cookie": cookie,
            "User-Agent": "PostmanRuntime-ApipostRuntime/1.1.0",
        }

    # ── 认证 ────────────────────────────────────────────────────

    async def _cookie(self) -> str:
        """为当前 token 返回一个新的 ``SESSION=...`` cookie。

        无缓存：每次调用都会用当前 ``guwpToken``（:meth:`set_token`
        设置）向登录端点重新认证。无 token 时返回空（匿名会话）。
        """
        token = self._guwp_token
        if not token:
            return ""

        import json

        login_url = f"{self.base_url}/api/v1/auth/third-party/login"
        body = json.dumps(
            {"loginMethod": "TOKEN", "platform": "GUWP", "token": token},
        )
        try:
            resp = await self._http().post(
                login_url,
                content=body,
                headers=self._headers(""),
            )
            resp.raise_for_status()
            new_session_id = resp.headers.get("x-session-id", "")
            if new_session_id:
                return f"SESSION={new_session_id}"
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Failed to refresh external skillhub cookie: %s",
                e,
            )
        return ""

    # ── SkillHubBase ─────────────────────────────────────────────

    async def list_skills(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> SkillHubPage:
        """浏览目录。``cursor`` 以 ``page:N`` 编码上游页码。"""
        import urllib.parse

        page = 0
        if cursor and cursor.startswith("page:"):
            try:
                page = int(cursor.split(":", 1)[1])
            except ValueError:
                page = 0

        url = (
            f"{self.base_url}{CATALOG_PATH}"
            f"?page={page}&q={urllib.parse.quote(q or '', safe='')}"
            f"&size={limit}&sort=&label=&namespace={CATALOG_NAMESPACE}"
        )
        try:
            resp = await self._http().get(
                url,
                headers=self._headers(await self._cookie()),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            status_code = getattr(getattr(e, "response", None), "status_code", 0)
            raise HubError(self.hub_id, status_code, str(e)) from e

        payload = data.get("data") or {}
        items = payload.get("items") or []
        total = payload.get("total") or 0

        cards = [
            self._to_card(item)
            for item in items
            if item.get("slug")
        ]
        next_cursor = f"page:{page + 1}" if (page + 1) * limit < total else None
        return SkillHubPage(
            cards=cards,
            next_cursor=next_cursor,
            total=total,
        )

    def _to_card(self, item: dict) -> SkillCard:
        """由一条目录记录构造 :class:`SkillCard`。"""
        slug = item["slug"]
        return SkillCard(
            hub_id=self.hub_id,
            id=slug,
            name=slug,
            description=item.get("summary", "") or "",
            metadata={
                k: v
                for k, v in item.items()
                if k not in ("slug", "summary")
            },
        )

    async def list_uploaded_skills(
        self,
        user_id: str,
        page: int = 0,
        size: int = 5,
    ) -> SkillHubPage:
        """浏览当前用户上传到 skillhub 的 skill。

        需先通过 :meth:`set_token` 设置 ``guwpToken``——端点按用户
        隔离，会话 cookie 携带身份。``page`` / ``size`` 以查询参数
        拼接到远程 URL（``?page=..&size=..``）。

        Args:
            user_id (`str`): 用户标识。
            page (`int`): 页码，默认 0。
            size (`int`): 每页数量，默认 5。
        """
        url = (
            f"{self.base_url}{MY_SKILLS_PATH}"
            f"?page={page}&size={size}"
        )
        try:
            resp = await self._http().get(
                url,
                headers=self._headers(await self._cookie()),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            status_code = getattr(getattr(e, "response", None), "status_code", 0)
            raise HubError(self.hub_id, status_code, str(e)) from e

        payload = data.get("data") or {}
        items = payload.get("items") or []
        total = payload.get("total")
        if total is None:
            total = len(items)

        return SkillHubPage(
            cards=[self._to_card(item) for item in items if item.get("slug")],
            next_cursor=None,
            total=total,
        )

    async def get_skill(self, user_id: str, card_id: str) -> SkillCard:
        """尚未实现。

        远程服务目前只暴露目录与下载端点；未接入单卡详情端点，因此
        通过本 hub 的“安装进库”（``POST /hub/skill/.../install``）会
        失败，直到实现为止。

        Raises:
            NotImplementedError: 当前恒抛。
        """
        raise NotImplementedError(
            "ExternalSkillHub.get_skill is not implemented yet — the "
            "remote skillhub exposes no single-card detail endpoint.",
        )

    async def download(
        self,
        user_id: str,
        card_id: str,
        version: str | None = None,
    ) -> SkillArchive:
        """打开 skill 归档流（``{base}/api/web/skills/global/<id>/download``）。

        响应头在此处等待——缺失的 skill（404）在调用方开始安装前抛出；
        body 保持惰性，归档可被直接管道送入 workspace 而无需整体驻留内存。
        """
        import urllib.parse

        url = (
            f"{self.base_url}{DOWNLOAD_PREFIX}/"
            f"{urllib.parse.quote(card_id, safe='')}/download"
        )
        client = self._http()
        stack = AsyncExitStack()
        try:
            response = await stack.enter_async_context(
                client.stream(
                    "GET",
                    url,
                    headers=self._headers(await self._cookie()),
                ),
            )
            if response.status_code == 404:
                raise KeyError(card_id)
            if response.status_code >= 400:
                body = await response.aread()
                raise HubError(
                    self.hub_id,
                    response.status_code,
                    body.decode("utf-8", errors="replace"),
                )
            return SkillArchive("zip", self._drain(stack, response))
        except Exception:
            await stack.aclose()
            raise

    async def _drain(
        self,
        stack: AsyncExitStack,
        response: Any,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        """逐块产出归档字节，结束时关闭流。"""
        try:
            async for chunk in response.aiter_bytes(chunk_size):
                yield chunk
        finally:
            await stack.aclose()
