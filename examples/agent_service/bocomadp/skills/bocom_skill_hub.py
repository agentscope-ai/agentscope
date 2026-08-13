# -*- coding: utf-8 -*-
"""Bocom skill hub 提供者。

对接 Bocom 技能服务（EAGP.EAGP-AGENT.V-1.0）的目录查询与下载接口，
通过 :class:`~agentscope.app.hub._skill._base.SkillHubBase` 暴露，
使 Web UI 与 workspace 流程将其视为普通 skill hub。

当前实现两个端点（其余暂不实现）：

    GET {base}/api/v1/skills?keyword=&userId=&page=&size=   → 目录查询
    GET {base}/api/v1/skills/global/<name>/download         → 下载 zip

服务地址从环境变量 ``BOCOMADP_BOCOM_SKILLHUB_URL`` 读取（默认
``http://12.235.193.172/EAGP.EAGP-AGENT.V-1.0``）；
业务用户标识从 ``BOCOMADP_BOCOM_SKILLHUB_USER_ID`` 读取（默认
``5000147900``）。

接入方式（main.py）：::

    skill_hubs=[..., BocomSkillHub(hub_id="external")]

``hub_id`` 需为 ``"external"`` 才能被 skill_router 的
``_external_hub`` 命中；或调整路由中的 hub key。
"""
from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, AsyncIterator

from agentscope._logging import logger
from agentscope.app.hub._error import HubError
from agentscope.app.hub._skill._base import SkillArchive, SkillHubBase

from ._card import SkillCard, SkillHubPage

if TYPE_CHECKING:
    import httpx

#: 目录查询端点路径。
CATALOG_PATH = "/api/v1/skills"

#: 下载端点前缀 —— 最终 URL 为 ``{base}{DOWNLOAD_PREFIX}/{name}/download``。
DOWNLOAD_PREFIX = "/api/v1/skills/global"

#: 默认流式块大小（64 KiB）。
DEFAULT_CHUNK_SIZE = 64 * 1024

#: 默认服务地址（未配置 ``BOCOMADP_BOCOM_SKILLHUB_URL`` 时使用）。
DEFAULT_BASE_URL = "http://12.235.193.172/EAGP.EAGP-AGENT.V-1.0"

#: 默认业务用户标识（未配置 ``BOCOMADP_BOCOM_SKILLHUB_USER_ID`` 时使用）。
DEFAULT_USER_ID = "5000147900"


def _default_base_url() -> str:
    """从环境变量读取 Bocom skillhub 地址（兼容 ``.env``），带默认值。

    环境变量未设置或为空字符串时，回退到默认地址。
    """
    return os.environ.get("BOCOMADP_BOCOM_SKILLHUB_URL", "").strip() or DEFAULT_BASE_URL


def _default_user_id() -> str:
    """从环境变量读取业务用户标识，带默认值。

    环境变量未设置或为空字符串时，回退到默认标识。
    """
    return os.environ.get("BOCOMADP_BOCOM_SKILLHUB_USER_ID", "").strip() or DEFAULT_USER_ID


class BocomSkillHub(SkillHubBase):
    """对接 Bocom 技能服务的 skill hub（查询 + 下载）。

    .. code-block:: python

        hub = BocomSkillHub()                 # base_url/user_id 取环境变量
        page = await hub.list_skills(user_id="zy", q="excel")
        archive = await hub.download(user_id="zy", "data-tag")
    """

    def __init__(
        self,
        hub_id: str = "bocom",
        display_name: str = "Bocom SkillHub",
        description: str = "Bocom 技能目录",
        icon_url: str | None = None,
        *,
        base_url: str | None = None,
        user_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        """初始化 Bocom skillhub 提供者。

        Args:
            hub_id (`str`): 路由中寻址该 hub 的稳定 id。
            display_name (`str`): 用户可见名称。
            description (`str`): 用户可见描述。
            icon_url (`str | None`): hub 图标。
            base_url (`str | None`): 服务地址；``None`` 时取
                ``BOCOMADP_BOCOM_SKILLHUB_URL``（或默认值）。
            user_id (`str | None`): 目录查询使用的业务用户标识；
                ``None`` 时取 ``BOCOMADP_BOCOM_SKILLHUB_USER_ID``。
            timeout (`float`): 单请求超时（秒）。
        """
        super().__init__(hub_id, display_name, description, icon_url)
        self.base_url = (base_url or _default_base_url()).rstrip("/")
        self.user_id = user_id or _default_user_id()
        self.timeout = timeout
        self._client: "httpx.AsyncClient | None" = None

    def set_user_id(self, user_id: str | None) -> None:
        """更新本次查询使用的业务用户标识（对应 Bocom 接口的 ``userId``）。

        可逐请求调用；未设置时使用构造参数/环境变量提供的默认值。
        """
        if user_id:
            self.user_id = user_id

    # ── 生命周期 ────────────────────────────────────────────────

    async def __aenter__(self) -> "BocomSkillHub":
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

    def _headers(self) -> dict[str, str]:
        """构造请求头。"""
        return {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "User-Agent": "PostmanRuntime-ApipostRuntime/1.1.0",
        }

    # ── SkillHubBase ─────────────────────────────────────────────

    async def list_skills(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
        loginName: str | None = None,
    ) -> SkillHubPage:
        """浏览目录。``cursor`` 以 ``page:N`` 编码上游页码。

        ``user_id`` 参数为框架层调用者标识，Bocom 业务用户标识由
        :attr:`self.user_id` 提供（构造参数或环境变量）。
        ``loginName`` 为可选业务登录名，透传给上游查询参数。
        """
        import urllib.parse

        page = 0
        if cursor and cursor.startswith("page:"):
            try:
                page = int(cursor.split(":", 1)[1])
            except ValueError:
                page = 0

        # 上游页码从 1 开始（示例 page=1），内部 cursor 从 0 计。
        upstream_page = max(page + 1, 1)
        url = (
            f"{self.base_url}{CATALOG_PATH}"
            f"?keyword={urllib.parse.quote(q or '', safe='')}"
            f"&userId={urllib.parse.quote(self.user_id, safe='')}"
        )
        if loginName:
            url += f"&loginName={urllib.parse.quote(loginName, safe='')}"
        url += f"&page={upstream_page}&size={limit}"
        try:
            resp = await self._http().get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            status_code = getattr(getattr(e, "response", None), "status_code", 0)
            raise HubError(self.hub_id, status_code, str(e)) from e

        items, total = self._extract_items(data)
        cards = [
            self._to_card(item)
            for item in items
            if isinstance(item, dict) and self._card_name(item)
        ]
        next_cursor = f"page:{page + 1}" if (page + 1) * limit < (total or 0) else None
        return SkillHubPage(
            cards=cards,
            next_cursor=next_cursor,
            total=total,
        )

    async def list_uploaded_skills(
        self,
        user_id: str,
        page: int = 0,
        size: int = 5,
    ) -> SkillHubPage:
        """暂不实现 —— 返回空页（避免路由层 AttributeError）。"""
        return SkillHubPage(cards=[], next_cursor=None, total=0)

    async def get_skill(self, user_id: str, card_id: str) -> SkillCard:
        """尚未实现。

        Bocom 服务当前只使用目录与下载端点。

        Raises:
            NotImplementedError: 当前恒抛。
        """
        raise NotImplementedError(
            "BocomSkillHub.get_skill is not implemented yet — the Bocom "
            "service exposes catalog and download endpoints only.",
        )

    async def download(
        self,
        user_id: str,
        card_id: str,
        version: str | None = None,
    ) -> SkillArchive:
        """打开 skill 归档流（``{base}/api/v1/skills/global/<name>/download``）。

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
                client.stream("GET", url, headers=self._headers()),
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

    # ── helpers ──────────────────────────────────────────────────

    @staticmethod
    def _extract_items(data: Any) -> tuple[list, int | None]:
        """从响应中宽容提取目录列表与总数。

        兼容 ``data.items``、``data.list``、``data.records``、``data``
        直接为列表等常见结构；总数取 ``total`` / ``totalCount`` / ``count``。
        """
        if isinstance(data, list):
            return data, None
        if not isinstance(data, dict):
            return [], None

        node = data
        for wrapper in ("data", "result", "content", "body"):
            if isinstance(node.get(wrapper), (dict, list)):
                node = node[wrapper]
                break

        if isinstance(node, list):
            return node, None
        if not isinstance(node, dict):
            return [], None

        for items_key in ("items", "list", "records", "rows", "content", "skills"):
            items = node.get(items_key)
            if isinstance(items, list):
                total = None
                for total_key in ("total", "totalCount", "count"):
                    if isinstance(node.get(total_key), int):
                        total = node[total_key]
                        break
                return items, total
        return [], None

    @staticmethod
    def _card_name(item: dict) -> str | None:
        """从一条目录记录提取 skill 名称（即下载 URL 的 data-tag）。"""
        for key in ("name", "skillName", "skill_name", "slug", "title"):
            value = item.get(key)
            if value:
                return str(value)
        return None

    def _to_card(self, item: dict) -> SkillCard:
        """由一条目录记录构造 :class:`SkillCard`。"""
        name = self._card_name(item)
        card_id = item.get("id") or item.get("skillId") or name
        description = (
            item.get("summary")
            or item.get("description")
            or item.get("desc")
            or ""
        )
        return SkillCard(
            hub_id=self.hub_id,
            id=str(card_id),
            name=name or "",
            description=str(description),
            metadata={k: v for k, v in item.items()},
        )

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
