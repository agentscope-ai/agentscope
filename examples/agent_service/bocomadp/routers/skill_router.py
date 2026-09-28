# -*- coding: utf-8 -*-
"""外部 skill hub 相关路由（迁移自 ``bankcomm_adp.routers.skill_router``）。

三个端点全部支持任意隔离策略（含 PER_SESSION）：workspace 一律通过
会话记录（DB 中持久化的 ``config.workspace_id``）解析，Bubblewrap 等
沙箱后端下精确指向对应会话的工作目录。
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.routing import APIRoute

from agentscope.app._router._workspace import (
    list_skills as _framework_list_skills,
)

from agentscope._logging import logger
from agentscope.app._service import ResourceAccessService
from agentscope.app.deps import (
    get_current_user_id,
    get_resource_access_service,
    get_skill_hubs,
    get_storage,
    get_workspace_manager,
)
from agentscope.app.hub import SkillHubBase
from agentscope.app.hub._error import HubError
from agentscope.app.storage import StorageBase
from agentscope.app.workspace_manager import WorkspaceManagerBase
from agentscope.skill import Skill

from ..agents_extend_store import (
    SKILL_TYPE_SKILLHUB,
    append_extend_entry,
    entry_display_name,
    entry_names,
    list_agent_skills,
    set_agent_skills,
    sync_extend_from_names,
)
from ..skills._oa_context import OA_HEADER, get_current_oa
from ..skills._schema import (
    AgentSkillsListResponse,
    EnsureSkillsRequest,
    SkillActionResponse,
    SkillInfo,
    parse_skill_ref,
)
from ..skills._skillhub_auth import (
    OA_SEPARATOR,
    build_auth_token,
    skillhub_platform,
)

skill_router = APIRouter(prefix="/workspace", tags=["skill-external"])

#: AES 凭证的有效期（秒）。上游登录接口按 5 分钟判定，且凭证明文含时间戳，
#: 因此必须「用前现造」，不可缓存（见 ``skills/_skillhub_auth`` 模块说明）。
AES_TOKEN_TTL_SECONDS = 300


def _raise_remote_error(payload: dict) -> None:
    """远端业务失败（HTTP 2xx 但 ``code != 0``）——取远端 ``code``/``msg`` 抛 400。

    远端用 ``code`` 表达业务结果（``0`` 成功），可能以 **HTTP 200 +
    ``code != 0``** 返回失败（如未登录、技能不存在），因此不能只看
    HTTP 状态码，必须解析 ``code``。
    """
    code = payload.get("code")
    if code in (0, "0", None):  # 0 / "0" = 成功；无 code 字段视为非信封结构，跳过
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": code,
            "msg": payload.get("msg") or "",
            "timestamp": payload.get("timestamp"),
            "requestId": payload.get("requestId"),
        },
    )


def _hub_error_to_502(e: HubError) -> HTTPException:
    """上游失败（非 2xx / 网络错误）→ 统一 502，detail 带远端真实状态码。"""
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=(
            f"Remote skillhub error (hub={e.hub_id}, "
            f"status={e.status_code}): {e.message}"
        ),
    )


async def _resolve_workspace(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
):
    """按会话记录解析其绑定的 workspace（含 PER_SESSION 语义）。

    从 DB 读取会话持久化的 ``config.workspace_id``，而非现算——
    沙箱后端据此定位对应会话的工作目录。
    """
    session_record = await storage.get_session(user_id, agent_id, session_id)
    if session_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id!r} not found.",
        )
    return await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
    )


def _external_hub(
    hubs: dict[str, SkillHubBase],
) -> SkillHubBase:
    """取注册的外部 skillhub，未注册则 404。"""
    hub = hubs.get("external") if hubs else None
    if hub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No 'external' skill hub is registered.",
        )
    return hub


def _set_token(hub: SkillHubBase, guwp_token: str | None) -> None:
    """逐请求刷新 hub 的 guwp-token（仅 :class:`BocomSkillHub` 有该方法）。

    ``ExternalSkillHub`` **没有** ``set_token``，它的鉴权是
    ``oa``（回退 ``X-User-ID``）→ AES 凭证 → session cookie，请求头里
    不带 guwp-token；因此对走 external 的端点（``/skill/ensure``、
    ``/skill/download/{namespace}:{name}``、``/skills/*``）而言，
    ``guwpToken`` 是**可选且当前无效**的——传不传行为一致。
    """
    set_token = getattr(hub, "set_token", None)
    if set_token is not None:
        set_token(guwp_token)


def _equipped_names(skills: list[Any]) -> set[str]:
    """已装备技能的"可匹配名"集合：agent-facing 名 ∪ 目录名。

    ``Skill.name`` 是 SKILL.md frontmatter 里的名字，而下载解压出来的
    目录名是 hub 的 slug（如 ``rollback-check-sql``）——两者可能不同
    （bocom 技能尤甚），所以**两路都要算**才判得准（与
    ``enable_agent_skill`` / ``enable_bocom_skill`` 的判定口径一致）。
    """
    names: set[str] = set()
    for skill in skills:
        name = getattr(skill, "name", "")
        if name:
            names.add(str(name))
        dir_name = os.path.basename(str(getattr(skill, "dir", "")).rstrip("/\\"))
        if dir_name:
            names.add(dir_name)
    return names


async def _install_skill_archive(
    workspace: Any,
    hubs: dict[str, SkillHubBase],
    oa: str,
    guwp_token: str | None,
    namespace: str,
    name: str,
) -> None:
    """从**外部** skillhub 下载一个技能并装进 workspace。

    安装一律走 external hub —— 与 ``POST /skill/download/{namespace}:{name}``
    是同一条路径、同一个远端接口
    （``{base}/api/web/skills/{namespace}/{name}/download``）；引用里 ``:``
    前的部分**原样作为远端 namespace 透传**（``global`` / ``bocom`` 都只是
    namespace，**不切换 hub**）。

    失败一律抛 ``HTTPException``（远端没有 → 404、安装失败原样抛出），
    由调用方决定"整体失败"还是"记进失败清单"。
    """
    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)
    try:
        archive = await hub.download(oa, name, namespace=namespace)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{name}' not found on the remote skillhub.",
        ) from None

    await workspace.add_skill_archive(
        archive.stream,
        archive.format,
        name,
    )


@asynccontextmanager
async def _pool_activity_guard(workspace: Any) -> AsyncIterator[None]:
    """安装期间把 workspace 标记为"使用中"（池 Pod 回收的护盾）。

    池模式（``SharedPvcK8sWorkspace``）的 sweeper 每 ``sweep_interval``
    扫一轮：全池最后活跃时间超 ``pool_idle_ttl`` 就回收 Pod（PVC 保留），
    框架的缓存 TTL 到期也会回收。活跃信号有两处——``set_run_active``
    （本进程 ``_pool_busy`` 据此跳过空闲回收）与 ``refresh_active``
    （写 Pod 的 last-active annotation，K8s 侧全局一致，多实例也认）；
    此前只有 agent run 经 ``SlotReleaseMiddleware`` 设置，**技能安装这类
    非 run 的写操作完全没有保护** → 安装中途 Pod 被回收，下一条 exec 报
    ``500, message='Invalid response status'``（命令根本没跑）。

    本地 / 按需模式的 workspace 没有这两个方法 → 自动降级为 no-op；
    两个调用都是"尽力而为"，失败只打 warning，绝不影响安装本身。

    Args:
        workspace (`Any`):
            已解析的会话工作区（通常是被 ``_WhitelistWorkspaceProxy``
            包了一层的真实 workspace，``__getattr__`` 会透传这两个方法）。
    """
    set_active = getattr(workspace, "set_run_active", None)
    refresh = getattr(workspace, "refresh_active", None)

    if set_active is not None:
        try:
            set_active(True)
        except Exception:  # noqa: BLE001
            logger.warning(
                "skill install: set_run_active(True) failed",
                exc_info=True,
            )
    if refresh is not None:
        try:
            await refresh()
        except Exception:  # noqa: BLE001
            logger.warning(
                "skill install: refresh_active failed",
                exc_info=True,
            )
    try:
        yield
    finally:
        # 顺序与 SlotReleaseMiddleware 一致：先刷活跃（空闲计时从"结束"
        # 重新起算），再清 busy 标记。
        if refresh is not None:
            try:
                await refresh()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "skill install: refresh_active (release) failed",
                    exc_info=True,
                )
        if set_active is not None:
            try:
                set_active(False)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "skill install: set_run_active(False) failed",
                    exc_info=True,
                )


async def _ensure_skills(
    workspace: Any,
    refs: list[str],
    hubs: dict[str, SkillHubBase],
    oa: str,
    guwp_token: str | None,
    *,
    storage: Any = None,
    agent_id: str = "",
) -> list[Skill]:
    """查缺补装：返回**补装后**的完整技能清单（与查询接口同形）。

    语义（"尽力而为"）：

    - 已装备的引用直接跳过，**不会**发起远端请求（幂等，可重复调用）；
    - 缺的逐个下载安装，**串行**执行（workspace 内部有 skill 锁，K8s 下
      还会争抢沙箱与远端配额）；
    - 单个失败**不中断**其余项，只打 warning —— 本函数只保证"把能装的
      装上"，返回值就是当前实际情况，因此失败信息走日志而非响应体
      （响应体必须与 ``GET /workspace/skill`` 完全同形）；
    - 每装成一个就**写穿 ``agents_extend``**（``storage`` + ``agent_id``
      都给了才写，见 :func:`_record_installed_skill`），使 ``/skills/*``
      的 ``used`` 标记之后不必再进沙箱；
    - 一个都没装成时直接返回上面那份列表，省一次沙箱列举往返。

    **已知取舍**：已装备判定按 **name**（frontmatter 名 ∪ 目录名）匹配，
    不带 namespace —— 与 ``/skill/download/*`` 的口径一致，也避免同名技能
    被反复重下；代价是 ``global:x`` 与 ``bocom:x`` 会被视为同一个技能。

    Args:
        workspace (`Any`):
            已解析的会话工作区（``list_skills`` / ``add_skill_archive``）。
        refs (`list[str]`):
            技能引用列表，元素 ``namespace:name``（模型层已校验）。
        hubs (`dict[str, SkillHubBase]`):
            已注册的 skillhub（安装只用到 ``external``）。
        oa (`str`):
            OA 账号（远端鉴权用；**这才是 external hub 真正的身份来源**）。
        guwp_token (`str | None`):
            逐请求透传给 hub 的 guwp-token；**当前对 external hub 无效**
            （它没有 ``set_token``，鉴权走 oa + session cookie），保留只为
            与 Bocom hub 共用同一套 helper。
        storage (`Any`):
            框架 storage（写穿 ``agents_extend`` 用）；``None`` = 不记录。
        agent_id (`str`):
            智能体 id（同上）；空串 = 不记录。

    Returns:
        `list[Skill]`:
            补装后工作区里的技能清单（``GET /workspace/skill`` 同形）。
    """
    existing = await workspace.list_skills()
    equipped = _equipped_names(existing)

    parsed = [parse_skill_ref(ref) for ref in refs]
    requested = {name for _, name in parsed}
    todo = [(ns, name) for ns, name in parsed if name not in equipped]
    skipped = len(parsed) - len(todo)

    installed = 0
    for namespace, name in todo:
        try:
            await _install_skill_archive(
                workspace,
                hubs,
                oa,
                guwp_token,
                namespace,
                name,
            )
        except Exception as e:  # noqa: BLE001 —— 单个失败不拖垮整体
            logger.warning(
                "skill ensure: failed to install '%s:%s': %s",
                namespace,
                name,
                e,
            )
            continue
        equipped.add(name)
        installed += 1
        if storage is not None and agent_id:
            await _record_installed_skill(
                storage,
                agent_id,
                namespace,
                name,
                source="skill-ensure",
            )

    failed = len(todo) - installed

    if installed == 0:
        # 没有落盘变化：直接返回上面那份列表（省一次沙箱列举往返）。
        # 注意 failed > 0 时上面每条失败都已单独打过 warning。
        logger.info(
            "skill ensure: requested=%d, skipped=%d, failed=%d, "
            "nothing installed",
            len(parsed),
            skipped,
            failed,
        )
        return existing

    refreshed = await workspace.list_skills()
    missing = sorted(requested - _equipped_names(refreshed))
    if missing:
        logger.warning(
            "skill ensure: requested but missing after install: %s",
            missing,
        )
    logger.info(
        "skill ensure: requested=%d, installed=%d, skipped=%d, failed=%d, "
        "missing=%d",
        len(parsed),
        installed,
        skipped,
        failed,
        len(missing),
    )
    return refreshed


def _card_version(metadata: dict) -> str:
    """取远端目录项里的版本号。

    只认**已发布版本** ``publishedVersion.version``（形如 ``v20260907.102346``）；
    远端该项为 ``null``（未发布）时返回空串，不回退到 ``headlineVersion``。
    ``external_hub._to_card()`` 把除 ``slug``/``summary`` 外的字段原样放进
    ``card.metadata``，所以这里直接读。
    """
    node = metadata.get("publishedVersion")
    if isinstance(node, dict) and node.get("version"):
        return str(node["version"])
    return ""


async def _session_used_names(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
) -> set[str]:
    """返回会话 workspace 中已装备的 skill 名集合。

    session 无效 → 404；其他解析失败 → 降级为空集（不拖垮查询）。
    """
    try:
        workspace = await _resolve_workspace(
            user_id,
            agent_id,
            session_id,
            storage,
            workspace_manager,
        )
        agent_skills = await workspace.list_skills()
        return {s.name for s in agent_skills}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Failed to resolve workspace for agent %s, marking no "
            "skills as used: %s",
            agent_id,
            e,
        )
        return set()


async def _used_names_from_table(
    storage: Any,
    agent_id: str,
) -> set[str] | None:
    """该 agent 的"已装技能名"——**只查 ``agents_extend`` 表，不申请 Pod**。

    - 表里有该 agent 的记录（含 ``{"skills": []}``）→ 返回名字集合
      （空集表示"确实一个都没装"，不是"不知道"）；
    - **无记录**（历史智能体、或未经表写入的路径）→ 返回 ``None``，
      调用方据此回源沙箱；
    - 读表失败 → 打 warning 并按无记录处理（降级，不拖垮查询）。
    """
    try:
        entries = await list_agent_skills(storage, agent_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "used skills: reading agents_extend failed for agent %s: %s",
            agent_id,
            e,
        )
        return None
    if entries is None:
        return None
    names: set[str] = set()
    for entry in entries:
        names |= entry_names(entry)
    return names


async def _used_names_for_agent(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
) -> set[str]:
    """该会话的"已装技能名"集合：**先查 ``agents_extend`` 表**，无记录才回源 workspace。

    表里有该 agent 的记录 → 直接用（**不解析会话、不申请 Pod**），这正是
    ``/skills/external`` / ``/skills/uploaded`` 的 ``used`` 标记不再依赖沙箱的
    原因；没有记录 → 回落 :func:`_session_used_names`，行为与改造前一致
    （session 无效 → 404；其他失败 → 空集）。
    """
    cached = await _used_names_from_table(storage, agent_id)
    if cached is not None:
        logger.info(
            "used skills: agent %s served from agents_extend (%d names)",
            agent_id,
            len(cached),
        )
        return cached
    return await _session_used_names(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )


async def _record_installed_skill(
    storage: Any,
    agent_id: str,
    namespace: str,
    name: str,
    *,
    source: str,
    listed_names: list[str] | None = None,
) -> None:
    """把"刚装上的技能"记进 ``agents_extend``（尽力而为，type 固定 ``skillhub``）。

    ``/skills/external`` / ``/skills/uploaded`` 的 ``used`` 标记以该表为准，
    因此安装成功后必须写穿，否则会出现"装了却显示未安装"。写成
    ``{"type": "skillhub", "name": "namespace:name"}``（与 ``/skill/ensure``
    入参同口径，可原样拼回）。

    分三种情况：

    - 表里**已有可用记录** → 单条追加（已存在不重复写）；
    - 表里**没有可用记录**且给了 ``listed_names``（该 agent 当前**完整技能
      清单**，调用方需排除本次装的这一个）→ **建行**：清单里其余名字按
      ``uploaded`` / ``unknown`` 落库，本次技能再标成 ``skillhub`` —— 既补上
      记录，又不会把该 agent 其它已装技能漏掉；
    - 没有可用记录又没给清单（如 ``/skill/ensure`` 的逐项安装）→ 不写，
      保持"无记录 → 读侧回源沙箱"的旧行为（单条信息不足以重建整份清单）。

    Args:
        storage (`Any`): 框架 storage。
        agent_id (`str`): 智能体 id。
        namespace (`str`): 本次安装的技能 namespace（= 远端 namespace/slug 前缀）。
        name (`str`): 本次安装的技能名（= hub slug / 解压目录名）。
        source (`str`): 仅用于日志的来源标记。
        listed_names (`list[str] | None`):
            该 agent 当前的**完整技能清单**（frontmatter 名），需已排除本次
            安装的这个技能；``None`` = 调用方没有清单，不建行。

    Note:
        失败只打 warning —— 记录失败不该影响安装结果（技能已经落盘）。
    """
    ref = {"type": SKILL_TYPE_SKILLHUB, "name": f"{namespace}:{name}"}
    try:
        if await list_agent_skills(storage, agent_id) is not None:
            await append_extend_entry(storage, agent_id, ref, source=source)
            return
        if listed_names is None:
            logger.info(
                "skill install: agent %s has no agents_extend record and no "
                "listing was supplied; skip recording %s:%s (queries fall "
                "back to the sandbox)",
                agent_id,
                namespace,
                name,
            )
            return
        # 没有可用记录 → 用完整清单建行（其余技能按 uploaded/unknown），
        # 再把本次技能标成 skillhub（create_if_missing=True 保证一定有行）。
        await sync_extend_from_names(
            storage,
            agent_id,
            listed_names,
            source=source,
        )
        await append_extend_entry(
            storage,
            agent_id,
            ref,
            source=source,
            create_if_missing=True,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "skill install: failed to record %s:%s into agents_extend for "
            "agent %s",
            namespace,
            name,
            agent_id,
            exc_info=True,
        )


@skill_router.get(
    "/skillhub/aes-token",
    summary="Get SkillHub AES credential",
    description=(
        "Locally generate the SkillHub third-party login credential "
        "(identical algorithm to the frontend ``lib/skillhub/token.ts``):\n"
        "``key = <Beijing date YYYYMMDD>(8) + <SKILLHUB_CHANNEL_RAND>(8)``,"
        " ``token = hex(AES-128-ECB/PKCS7(\"{oa}#{13-digit ms timestamp}\"))``.\n"
        "``oa`` defaults to the ``oa`` header, then ``X-User-ID``. The "
        "credential is "
        "only valid for ~5 minutes, so it is built on **every** call — "
        "never cache it."
    ),
)
async def get_skillhub_aes_token(
    oa: str | None = Query(
        default=None,
        description="OA 账号；省略时依次取 oa 请求头 / X-User-ID",
    ),
    oa_header: str | None = Header(default=None, alias=OA_HEADER),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """本地生成 SkillHub 登录用的 AES 凭证（明文只含 OA 与毫秒时间戳）。

    - ``oa`` 省略时依次取 ``oa`` 请求头 → ``X-User-ID``；为空 → 400，
      含分隔符 ``#`` → 422
    - 密钥后 8 字节取 ``SKILLHUB_CHANNEL_RAND``（必须 8 个 ASCII 字符）；
      长度不为 16 字节 → 500（配置错误，非调用方问题）
    - 返回 ``{oa, token, platform, timestamp, expires_in}``：
      ``token`` 直接作为登录请求体的 ``token``，``platform`` 即登录请求体的
      ``platform`` 取值（``SKILLHUB_PLATFORM``），``timestamp`` 为明文里用到
      的毫秒时间戳，``expires_in`` 为有效期秒数（仅供调用方判断是否过期）

    ``token`` 含登录态语义、有效期约 5 分钟：**每次调用现造**，调用方拿到后
    应立即使用，不要落库/缓存。
    """
    account = (oa or oa_header or user_id).strip()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An OA account is required: pass ?oa= or X-User-ID.",
        )
    if OA_SEPARATOR in account:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"OA account must not contain {OA_SEPARATOR!r}.",
        )

    # 密钥里的日期与明文里的时间戳共用同一个 ms 时间戳，避免跨零点不一致。
    timestamp_ms = int(time.time() * 1000)
    try:
        token = build_auth_token(account, timestamp_ms=timestamp_ms)
    except ValueError as e:
        # OA 已在上面校验，此处只可能是密钥长度问题（channel rand 配置错）
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SkillHub AES credential misconfigured: {e}",
        ) from e

    return {
        "oa": account,
        "token": token,
        "platform": skillhub_platform(),
        "timestamp": timestamp_ms,
        "expires_in": AES_TOKEN_TTL_SECONDS,
    }


@skill_router.get(
    "/skills/external",
    summary="Get Agent Skills",
    description=(
        "Query the external skillhub catalog. Two response modes:\n"
        "- With BOTH ``agent_id`` and ``session_id``: legacy processed "
        "shape ``{skills: [{name, category, description, version, used}], "
        "total}`` — ``category`` is the remote ``namespace``, "
        "``version`` is the catalog item's **published** version (empty "
        "string when the remote reports ``publishedVersion: null``), ``used`` "
        "marks skills already installed for this agent, **read from the "
        "``agents_extend`` table (no sandbox / Pod required)**; only when the "
        "agent has no such record does it fall back to the session's "
        "workspace.\n"
        "- Otherwise: the remote response verbatim "
        "(``{code, msg, data:{items, total, page, size}, timestamp, "
        "requestId}``)."
    ),
)
async def get_agent_skills(
    agent_id: str | None = Query(default=None),
    session_id: str = Query(default=""),
    page: int = Query(default=0, ge=0),
    q: str = Query(default=""),
    size: int = Query(default=10, ge=1, le=200),
    sort: str = Query(default=""),
    label: str = Query(
        default="",
        description="标签 slug（来自 /skills/labels），透传远端按标签过滤",
    ),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    oa: str = Depends(get_current_oa),
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> dict:
    """查询外部 skillhub 目录，两种返回模式。

    - ``agent_id`` 与 ``session_id`` **同时提供** → 旧格式
      ``{skills: [{name, category, description, version, used}], total}``：
      ``category`` 取远端 ``namespace``，``version`` 取目录项的
      ``publishedVersion.version``（远端为 ``null`` 时给空串），
      ``used`` 标记该智能体**已装过**的技能 —— 先查 ``agents_extend`` 表
      （**不申请 Pod**，也不解析会话），表内无该 agent 记录时才回源会话
      workspace（那时 session 无效 → 404）。
    - 任一缺失 → 远端响应**原样透传**（``{code, msg, data, ...}``）。

    ``agent_id`` 传了才做归属校验（不属于调用者则 404）。
    ``q`` 为关键字；``label`` 为标签 slug（远端过滤）；``sort`` 透传。
    """
    # 归属校验（仅当传了 agent_id）：不属于调用者则 404。
    if agent_id:
        await access.resolve_agent(user_id, agent_id)

    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)

    if agent_id and session_id:
        # ── 旧格式：加工 + used 标记（category 取远端 namespace）──
        # used 判定先查 agents_extend（不申请 Pod），无记录才回源 workspace。
        used_names = await _used_names_for_agent(
            user_id,
            agent_id,
            session_id,
            storage,
            workspace_manager,
        )
        cursor = f"page:{page}" if page else None
        try:
            page_result = await hub.list_skills(
                oa,
                q=q or None,
                cursor=cursor,
                limit=size,
                label=label or None,
                sort=sort or None,
            )
        except HubError as e:
            raise _hub_error_to_502(e) from e

        skills_list = [
            SkillInfo(
                name=card.name,
                category=card.metadata.get("namespace") or "public",
                description=card.description or "",
                version=_card_version(card.metadata),
                used=card.name in used_names,
            )
            for card in page_result.cards
        ]
        return AgentSkillsListResponse(
            skills=skills_list,
            total=(
                page_result.total
                if page_result.total is not None
                else len(skills_list)
            ),
        ).model_dump()

    # ── 原样透传 ──
    list_raw = getattr(hub, "list_skills_raw", None)
    if list_raw is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "The 'external' skill hub does not support raw catalog "
                "query (list_skills_raw)."
            ),
        )

    # q = 关键字搜索；label = 标签 slug（来自 /skills/labels），透传远端
    # 按标签过滤；sort 透传远端。三者互不干扰。
    try:
        return await list_raw(
            oa,
            q=q or None,
            page=page,
            limit=size,
            label=label or None,
            sort=sort or None,
        )
    except HubError as e:
        raise _hub_error_to_502(e) from e


@skill_router.get(
    "/skills/bocom",
    response_model=AgentSkillsListResponse,
    summary="Get Bocom Skills",
    description=(
        "Query the Bocom skillhub catalog and return it to the "
        "frontend with the same shape as the external skillhub, "
        "marking as ``used`` the skills already present in the "
        "session's workspace (``agent_id`` and ``session_id`` are "
        "passed as query parameters)."
    ),
)
async def get_bocom_skills(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    keyword: str = Query(default=""),
    status: str = Query(default="PUBLISHED"),
    namespace: str = Query(default="global"),
    labelSlugs: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    myOnly: bool = Query(default=False),
    size: int = Query(default=10, ge=1, le=200),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    oa: str = Depends(get_current_oa),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> AgentSkillsListResponse:
    """查询 Bocom skillhub 目录并返回（返回格式与 external hub 一致）。

    参数与 Bocom 上游 curl 请求对齐：``keyword`` / ``status`` /
    ``namespace`` / ``labelSlugs`` / ``page`` / ``myOnly`` / ``size``；
    ``agent_id`` / ``session_id`` 用于解析 workspace 以标记 ``used`` 状态。
    """
    # 归属校验：agent 必须属于（或被共享给）调用者，否则 404。
    await access.resolve_agent(user_id, agent_id)

    used_names = await _session_used_names(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )

    hub = hubs.get("bocom") if hubs else None
    if hub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No 'bocom' skill hub is registered.",
        )

    # 透传 guwp-token（逐请求设置，与 external hub 的 set_token 一致）。
    set_token = getattr(hub, "set_token", None)
    if set_token is not None:
        set_token(guwp_token)

    page_result = await hub.list_skills(
        oa,
        keyword=keyword,
        status=status,
        namespace=namespace,
        labelSlugs=labelSlugs,
        page=page,
        myOnly=myOnly,
        size=size,
    )

    skills_list = [
        SkillInfo(
            name=card.name,
            category=card.tags[0] if card.tags else "global",
            description=card.description or "",
            used=card.name in used_names,
        )
        for card in page_result.cards
    ]
    return AgentSkillsListResponse(
        skills=skills_list,
        total=(
            page_result.total
            if page_result.total is not None
            else len(skills_list)
        ),
    )


@skill_router.get(
    "/skills/uploaded",
    summary="Get Uploaded Skills",
    description=(
        "Query the external skillhub for the skills the caller uploaded. "
        "Two response modes:\n"
        "- With BOTH ``agent_id`` and ``session_id``: legacy processed "
        "shape ``{skills: [{name, category, description, version, used}], "
        "total}`` (``category`` is the remote ``namespace``, ``version`` "
        "the published version, empty when ``publishedVersion`` is null; "
        "``used`` comes from ``agents_extend`` — no sandbox / Pod needed).\n"
        "- Otherwise: the remote response verbatim. ``guwpToken`` is "
        "required in both modes (user-scoped endpoint)."
    ),
)
async def get_uploaded_skills(
    agent_id: str | None = Query(default=None),
    session_id: str = Query(default=""),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=5, ge=1, le=200),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    oa: str = Depends(get_current_oa),
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> dict:
    """返回调用者上传到外部 skillhub 的 skill，两种返回模式。

    - ``agent_id`` 与 ``session_id`` **同时提供** → 旧格式
      ``{skills: [{name, category, description, version, used}], total}``；
      ``used`` 与 ``/skills/external`` 同源：先查 ``agents_extend`` 表
      （**不申请 Pod**），表内无记录才回源会话 workspace。
    - 任一缺失 → 远端响应**原样透传**。

    ``guwpToken`` 两种模式都**必填**（用户级端点）。
    ``agent_id`` 传了才做归属校验。
    """
    # 归属校验（仅当传了 agent_id）：不属于调用者则 404。
    if agent_id:
        await access.resolve_agent(user_id, agent_id)

    if not guwp_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="guwpToken header is required for uploaded skills.",
        )

    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)

    if agent_id and session_id:
        # ── 旧格式：加工 + used 标记（category 取远端 namespace）──
        # used 判定先查 agents_extend（不申请 Pod），无记录才回源 workspace。
        used_names = await _used_names_for_agent(
            user_id,
            agent_id,
            session_id,
            storage,
            workspace_manager,
        )
        try:
            page_result = await hub.list_uploaded_skills(
                oa,
                page=page,
                size=size,
            )
        except HubError as e:
            raise _hub_error_to_502(e) from e

        skills_list = [
            SkillInfo(
                name=card.name,
                category=card.metadata.get("namespace") or "public",
                description=card.description or "",
                version=_card_version(card.metadata),
                used=card.name in used_names,
            )
            for card in page_result.cards
        ]
        return AgentSkillsListResponse(
            skills=skills_list,
            total=(
                page_result.total
                if page_result.total is not None
                else len(skills_list)
            ),
        ).model_dump()

    # ── 原样透传 ──
    list_raw = getattr(hub, "list_uploaded_skills_raw", None)
    if list_raw is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "The 'external' skill hub does not support raw uploaded "
                "query (list_uploaded_skills_raw)."
            ),
        )
    try:
        return await list_raw(oa, page=page, size=size)
    except HubError as e:
        raise _hub_error_to_502(e) from e


@skill_router.get(
    "/skills/labels",
    summary="Get Skill Labels",
    description=(
        "Query all skill category labels from the remote skillhub "
        "(``GET {base}/api/web/labels``) and return the remote "
        "response **verbatim**. ``data`` is a two-level tree array "
        "(``level`` 1 categories with nested ``children``; ``type`` is "
        "``RECOMMENDED`` / ``PRIVILEGED``). ``guwpToken`` is optional "
        "— passing it may additionally surface ``PRIVILEGED`` labels."
    ),
)
async def get_skill_labels(
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    oa: str = Depends(get_current_oa),
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> dict:
    """返回外部 skillhub 的全部分类标签，**原样返回远端响应**。

    - 无分页（全量返回），``data`` 为树形数组
    - ``guwpToken`` **可选**：带登录态可能额外返回 ``PRIVILEGED`` 类目
    - 业务失败（``code != 0``）→ 400 + 远端 ``code``/``msg``
    - 网络 / HTTP 失败 → 502
    """
    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)

    list_raw = getattr(hub, "list_labels_raw", None)
    if list_raw is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "The 'external' skill hub does not support raw labels "
                "query (list_labels_raw)."
            ),
        )
    try:
        return await list_raw(oa)
    except HubError as e:
        raise _hub_error_to_502(e) from e


@skill_router.get(
    "/skills/starred",
    summary="Get Starred Skills",
    description=(
        "Query the external skillhub for the skills the caller starred "
        "and return the remote response **verbatim** "
        "(``{code, msg, data:{items, total, page, size}, ...}``). "
        "``guwpToken`` is required (user-scoped); ``agent_id`` is "
        "optional — when given, its ownership is verified."
    ),
)
async def get_starred_skills(
    agent_id: str | None = Query(default=None),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=5, ge=1, le=200),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    oa: str = Depends(get_current_oa),
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> dict:
    """返回调用者在外部 skillhub 收藏的 skill，**原样返回远端响应**。

    收藏是「用户级」数据，与具体智能体无关，因此：
    - ``agent_id`` **非必填**；传了才做归属校验（必须属于或被共享给
      调用者，否则 404）
    - ``guwpToken`` **必填**——端点按用户隔离，由其派生的会话 cookie
      携带远程身份

    支持 ``page`` / ``size`` 分页（透传给远端 ``?page=&size=``）。
    """
    # 归属校验（仅当传了 agent_id 时）：不属于调用者则 404。
    if agent_id:
        await access.resolve_agent(user_id, agent_id)

    if not guwp_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="guwpToken header is required for starred skills.",
        )

    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)

    list_raw = getattr(hub, "list_starred_skills_raw", None)
    if list_raw is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "The 'external' skill hub does not support raw starred "
                "query (list_starred_skills_raw)."
            ),
        )
    try:
        return await list_raw(oa, page=page, size=size)
    except HubError as e:
        raise _hub_error_to_502(e) from e


@skill_router.get(
    "/skill/markdown",
    summary="Get Skill Markdown",
    description=(
        "Fetch a skill's file content (default ``SKILL.md``) from the "
        "remote skillhub: ``GET {base}/api/web/skills/{namespace}/"
        "{slug}/versions/{version}/file?path=SKILL.md``. The remote "
        "returns **plain-text markdown**, wrapped here as "
        "``{\"content\": \"...\"}``. ``slug`` and ``version`` are "
        "required; a leading ``v`` in ``version`` is stripped "
        "automatically."
    ),
)
async def get_skill_markdown(
    slug: str = Query(..., description="技能 slug（目录项 slug）"),
    version: str = Query(
        ...,
        description="版本号（如 v20260904.074311，前导 v 会自动去除）",
    ),
    namespace: str = Query(default="global", description="命名空间"),
    path: str = Query(default="SKILL.md", description="技能内文件路径"),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    oa: str = Depends(get_current_oa),
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> dict:
    """读取技能的 ``SKILL.md``（或指定文件）内容。

    - ``slug`` / ``version`` **必填**，缺一 → 422
    - ``namespace`` 默认 ``global``；``path`` 默认 ``SKILL.md``
    - ``guwpToken`` **可选**：公共 skill 可匿名读，私有 skill 需携带
    - 成功返回 ``{"content": "<markdown 原文>"}``
    - 远端以 JSON 信封返回业务失败（``code != 0``）→ 400 + 远端 code/msg
    - 网络 / HTTP 失败 → 502
    """
    if not slug.strip() or not version.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Both 'slug' and 'version' are required.",
        )

    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)

    get_file = getattr(hub, "get_skill_file", None)
    if get_file is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "The 'external' skill hub does not support reading "
                "skill files (get_skill_file)."
            ),
        )

    try:
        content = await get_file(
            oa,
            slug=slug,
            version=version,
            namespace=namespace,
            path=path,
        )
    except HubError as e:
        raise _hub_error_to_502(e) from e

    # 远端正常情况下返回纯文本 markdown；若改为 JSON 信封（含 code），
    # 则走统一的业务错误映射并把信封原样透传。
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict) and "code" in payload:
        _raise_remote_error(payload)
        return payload

    return {"content": content}


@skill_router.put(
    "/skill/star/{skill_id}",
    summary="Star a Skill",
    description=(
        "Star (favorite) a skill on the remote skillhub: "
        "``PUT {base}/api/web/skills/{id}/star``. ``skill_id`` is the "
        "remote **numeric id** (from ``items[].id``), not the slug. "
        "``guwpToken`` is required. The remote response is returned "
        "verbatim; a business failure (HTTP 200 with ``code != 0``) is "
        "surfaced as 400 with the remote ``code``/``msg``."
    ),
)
async def star_skill(
    skill_id: str,
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    oa: str = Depends(get_current_oa),
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> dict:
    """收藏一个 skill（用户级行为，与智能体无关）。

    - ``skill_id``：远端技能的**数字 id**（目录透传后的 ``items[].id``）
    - ``guwpToken``：**必填**（缺 → 400），用于换取会话 cookie
    - 成功：原样返回 ``{"code": 0, "msg": "Updated successfully", ...}``
    - 业务失败：``code != 0`` → 400 + 远端 ``code``/``msg``
    - 网络/HTTP 失败：502
    """
    if not guwp_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="guwpToken header is required for starring a skill.",
        )

    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)

    star = getattr(hub, "star_skill", None)
    if star is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="The 'external' skill hub does not support starring.",
        )

    try:
        result = await star(oa, skill_id)
    except HubError as e:
        raise _hub_error_to_502(e) from e

    _raise_remote_error(result)
    logger.info(
        "skill star: user=%s skill_id=%s result_code=%s",
        user_id,
        skill_id,
        result.get("code"),
    )
    return result


@skill_router.delete(
    "/skill/star/{skill_id}",
    summary="Unstar a Skill",
    description=(
        "Unstar a skill on the remote skillhub: "
        "``DELETE {base}/api/web/skills/{id}/star``. Same contract as "
        "the starring endpoint."
    ),
)
async def unstar_skill(
    skill_id: str,
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    oa: str = Depends(get_current_oa),
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> dict:
    """取消收藏一个 skill（语义同 :func:`star_skill`）。"""
    if not guwp_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="guwpToken header is required for unstarring a skill.",
        )

    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)

    unstar = getattr(hub, "unstar_skill", None)
    if unstar is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="The 'external' skill hub does not support unstarring.",
        )

    try:
        result = await unstar(oa, skill_id)
    except HubError as e:
        raise _hub_error_to_502(e) from e

    _raise_remote_error(result)
    logger.info(
        "skill unstar: user=%s skill_id=%s result_code=%s",
        user_id,
        skill_id,
        result.get("code"),
    )
    return result


@skill_router.post(
    "/skill/download/{skill_full_name}",
    response_model=SkillActionResponse,
    summary="Enable Skill for Agent",
    description=(
        "Download a skill from the remote skillhub into the session's "
        "workspace. ``skill_full_name`` follows ``namespace:name`` "
        "(e.g. ``global:rollback-check-sql``) — the namespace is "
        "required and is used in the remote download URL "
        "``/api/web/skills/{namespace}/{name}/download``. ``agent_id`` "
        "and ``session_id`` are passed as query parameters."
    ),
)
async def enable_agent_skill(
    skill_full_name: str,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    oa: str = Depends(get_current_oa),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> SkillActionResponse:
    """为指定 agent 启用（下载安装）一个 skill。

    ``skill_full_name`` 遵循 ``namespace:name`` 约定（如
    ``global:rollback-check-sql``）——**namespace 必传**（即目录项的
    ``namespace`` 字段），远端下载 URL 为
    ``/api/web/skills/{namespace}/{name}/download``。已装备时幂等返回。
    目标 workspace 从持久化会话记录解析，任意隔离策略下都精确。

    装成功后**写穿 ``agents_extend``**（``{"type": "skillhub", "name":
    "namespace:name"}``）：``/skills/external``、``/skills/uploaded`` 的
    ``used`` 标记以该表为准；表里原本没有该 agent 的记录时，用本次拿到的工作
    区清单建行（其余技能按 ``uploaded``/``unknown``），本次技能标
    ``skillhub``。写表失败只打 warning，不影响安装结果。
    """
    await access.resolve_agent(user_id, agent_id)

    if ":" not in skill_full_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "skill_full_name must be in 'namespace:name' form, "
                "e.g. 'global:rollback-check-sql'."
            ),
        )
    category, skill_name = skill_full_name.split(":", 1)

    # PER_SESSION 下 workspace id 是每会话随机值，必须来自数据库而非现算。
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )

    # 已装备 —— 幂等成功。按 agent-facing 名或目录名匹配（frontmatter
    # 名可能与 slug 不同）。
    existing = await workspace.list_skills()
    if skill_name in {s.name for s in existing} or any(
        os.path.basename(s.dir.rstrip("/\\")) == skill_name
        for s in existing
    ):
        logger.info(
            "Skill '%s' already equipped in agent %s's workspace",
            skill_full_name,
            agent_id,
        )
        return SkillActionResponse(
            success=True,
            action="enabled",
            skill_id=skill_full_name,
        )

    # 经 hub 抽象下载 —— 归档流式送入 workspace 后端（沙箱兼容）。
    # namespace 由前端经 skill_full_name 的前缀传入，透传远端。
    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)
    try:
        archive = await hub.download(oa, skill_name, namespace=category)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_name}' not found on the remote skillhub.",
        ) from None
    try:
        # 安装期间置"使用中"：否则 sweeper 可能把正在写的池 Pod 当空闲
        # 回收，下一条 exec 被 apiserver 拒 → 500 Invalid response status。
        async with _pool_activity_guard(workspace):
            await workspace.add_skill_archive(
                archive.stream,
                archive.format,
                skill_name,
            )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to install skill '{skill_name}': {e}",
        ) from e

    # 校验确有新 skill 落盘（解压目录可能按 frontmatter 命名）。
    refreshed = await workspace.list_skills()
    new_names = {s.name for s in refreshed} - {s.name for s in existing}
    if not new_names:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Downloaded skill '{skill_name}' has no valid SKILL.md "
                "(requires 'name' and 'description' fields)."
            ),
        )

    # 该 agent 当前的完整清单（frontmatter 名），**排除本次装的这一个**：
    # `new_names` 就是本次下载新出现的技能（frontmatter 名）——无论沙箱把
    # 解压目录命名成 hub slug 还是 frontmatter 名，都能准确排除，免得建行时
    # 同一个技能既记成 uploaded/unknown 又记成 skillhub（读侧会当成两个）。
    skip = {skill_name} | new_names
    listed_others = [s.name for s in refreshed if s.name and s.name not in skip]

    # 写穿 agents_extend：/skills/external、/skills/uploaded 的 used 标记
    # 以该表为准（不申请 Pod）；表里没有记录时用上面这份清单建行，本次技能
    # 标 type=skillhub，见 helper 说明。
    await _record_installed_skill(
        storage,
        agent_id,
        category,
        skill_name,
        source="skill-download",
        listed_names=listed_others,
    )

    logger.info("Enabled skill '%s' for agent '%s'", skill_full_name, agent_id)
    return SkillActionResponse(
        success=True,
        action="enabled",
        skill_id=skill_full_name,
    )


@skill_router.post(
    "/skill/download/bocom/{skill_name}",
    response_model=SkillActionResponse,
    summary="Enable Bocom Skill for Agent",
    description=(
        "Download a skill from the Bocom skillhub into the session's "
        "workspace. ``skill_name`` is the Bocom skill name (e.g. "
        "``excel智能分析``); ``agent_id`` and ``session_id`` are passed "
        "as query parameters."
    ),
)
async def enable_bocom_skill(
    skill_name: str,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    namespaceSlug: str = Query(default="Global"),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    oa: str = Depends(get_current_oa),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> SkillActionResponse:
    """为指定 agent 启用（下载安装）一个 Bocom skill。

    ``skill_name`` 为 Bocom 技能名（如 ``excel智能分析``），直接从
    ``bocom`` hub 下载归档并安装进会话 workspace。已装备时幂等返回。
    目标 workspace 从持久化会话记录解析，任意隔离策略下都精确。
    """
    await access.resolve_agent(user_id, agent_id)

    # PER_SESSION 下 workspace id 是每会话随机值，必须来自数据库而非现算。
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )

    # 已装备 —— 幂等成功。按 agent-facing 名或目录名匹配（frontmatter
    # 名可能与 slug 不同）。
    existing = await workspace.list_skills()
    if skill_name in {s.name for s in existing} or any(
        os.path.basename(s.dir.rstrip("/\\")) == skill_name
        for s in existing
    ):
        logger.info(
            "Bocom skill '%s' already equipped in agent %s's workspace",
            skill_name,
            agent_id,
        )
        return SkillActionResponse(
            success=True,
            action="enabled",
            skill_id=skill_name,
        )

    # 从 bocom hub 下载 —— 归档流式送入 workspace 后端（沙箱兼容）。
    hub = hubs.get("bocom") if hubs else None
    if hub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No 'bocom' skill hub is registered.",
        )
    _set_token(hub, guwp_token)
    try:
        archive = await hub.download(
            oa,
            name=skill_name,
            namespaceSlug=namespaceSlug,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_name}' not found on the Bocom skillhub.",
        ) from None
    try:
        # 同 /skill/download/{namespace}:{name}：安装期间不许 sweeper 回收池 Pod。
        async with _pool_activity_guard(workspace):
            await workspace.add_skill_archive(
                archive.stream,
                archive.format,
                skill_name,
            )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to install skill '{skill_name}': {e}",
        ) from e

    # 校验确有新 skill 落盘（解压目录可能按 frontmatter 命名）。
    refreshed = await workspace.list_skills()
    new_names = {s.name for s in refreshed} - {s.name for s in existing}
    if not new_names:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Downloaded skill '{skill_name}' has no valid SKILL.md "
                "(requires 'name' and 'description' fields)."
            ),
        )

    # 写穿 agents_extend（同 /skill/download/{namespace}:{name}）：used 判定
    # 从表读，装完必须记上。
    await _record_installed_skill(
        storage,
        agent_id,
        namespaceSlug,
        skill_name,
        source="skill-download-bocom",
    )

    logger.info("Enabled Bocom skill '%s' for agent '%s'", skill_name, agent_id)
    return SkillActionResponse(
        success=True,
        action="enabled",
        skill_id=skill_name,
    )


@skill_router.post(
    "/skill/ensure",
    summary="Ensure Skills Installed (install the missing ones)",
    description=(
        "Given a list of skill refs (``namespace:name``), make sure they are "
        "all present in the session's workspace: the ones already equipped "
        "are skipped, the missing ones are downloaded from the **external** "
        "skillhub (``{base}/api/web/skills/{namespace}/{name}/download``, "
        "same path as ``POST /skill/download/{namespace}:{name}`` — the part "
        "before ':' is passed through as the remote namespace) and "
        "installed. "
        "**The response body is byte-identical in shape to "
        "``GET /workspace/skill``** (``[{name, description, dir, markdown, "
        "updated_at}]``) — it is the workspace's skill list *after* the "
        "ensure step, so the caller can render it directly. Per-skill "
        "failures do not fail the request (they are logged); a malformed "
        "ref fails the request with 422."
    ),
)
async def ensure_agent_skills(
    body: EnsureSkillsRequest,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    oa: str = Depends(get_current_oa),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> list[Skill]:
    """查缺补装：确保 ``body.skills`` 里的技能都在该工作区里。

    典型用法是接在复制之后——``POST /agent/{id}/copy`` 返回的 ``skills``
    直接作为本接口的请求体，一步把模板声明的技能补齐：

    ```text
    POST /api/workspace/skill/ensure?agent_id=<新id>&session_id=<会话>
    {"skills": ["global:rollback-check-sql", "bocom:excel智能分析"]}
    → 200 [{"name": "...", "description": "...", "dir": "...",
            "markdown": "...", "updated_at": 0.0}, ...]
    ```

    行为：

    - **幂等**：已装备的引用不请求远端（判定按 frontmatter 名 ∪ 目录名，
      与 ``/skill/download/*`` 一致）；可安全重复调用；
    - **响应体与 ``GET /workspace/skill`` 完全同形**（``list[Skill]``），
      是补装**之后**的清单，前端可直接渲染；``body.skills`` 为空时等价于
      一次普通查询；
    - **单点失败不报错**：远端 404 / 沙箱不可用 / 解压出无效 SKILL.md
      只写 warning 日志，其余项照装（响应里看不到失败明细——要保持与查询
      接口同形；需要明细就调 ``GET /workspace/skill`` 自行比对）；
    - **安装期间不被回收**：整段补装过程给 workspace 打"使用中"标记
      （池模式 ``set_run_active`` + ``refresh_active``，见
      :func:`_pool_activity_guard`），避免 sweeper 把正在写的池 Pod 当
      空闲回收；
    - 引用格式非法（缺 ``namespace``、含 ``/`` 或 ``..``）→ **422**
      （模型层校验），不会进沙箱。

    Args:
        body (`EnsureSkillsRequest`):
            期望安装的技能引用清单（``namespace:name``）。
        agent_id (`str`):
            智能体 id（query）。
        session_id (`str`):
            会话 id（query）——workspace 由会话记录解析，任意隔离策略下都准。
        guwp_token (`str | None`):
            逐请求透传给 hub 的 token。
        oa (`str`):
            Injected OA account.
        user_id (`str`):
            Injected authenticated user ID.
        storage (`StorageBase`):
            Injected storage backend.
        access (`ResourceAccessService`):
            Injected access service（可见性校验）。
        workspace_manager (`WorkspaceManagerBase`):
            Injected workspace manager.
        hubs (`dict[str, SkillHubBase]`):
            Injected skill hubs（安装只用 ``external``）。

    Returns:
        `list[Skill]`:
            补装后的技能清单。

    Raises:
        `HTTPException`:
            404 if the agent is not visible / the session does not exist;
            422 if a skill ref is malformed.
    """
    await access.resolve_agent(user_id, agent_id)
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    # 整个补装过程置"使用中"：技能安装不是 agent run，没有
    # SlotReleaseMiddleware 那层保护，否则 sweeper 可能中途回收池 Pod
    # （表现为某几个技能 failed、exec 报 500 Invalid response status）。
    async with _pool_activity_guard(workspace):
        return await _ensure_skills(
            workspace,
            body.skills,
            hubs,
            oa,
            guwp_token,
            storage=storage,
            agent_id=agent_id,
        )


# ---------------------------------------------------------------------------
# GET /workspace/skill 覆盖：先查 agents_extend，无记录才回源沙箱并写回
# ---------------------------------------------------------------------------

#: ``app.state`` 上的安装标记（幂等用）。
_SKILL_LIST_INSTALLED_FLAG = "_bocomadp_skill_list_cached_installed"


def _extend_entry_to_skill(entry: dict) -> Skill:
    """``agents_extend`` 的条目 → 框架 ``Skill``。

    ``skillhub`` 的 ``name`` 存的是完整引用 ``namespace:name``，而框架
    ``Skill.name`` 是 agent-facing 名（无命名空间），这里取冒号后半段对齐；
    其余类型（``uploaded`` / ``unknown``）原样使用。

    表里只有 ``type`` / ``name``，所以 ``description`` / ``dir`` /
    ``markdown`` 一律空串、``updated_at`` 为 ``0.0``（与调用方约定一致）。
    """
    name = entry_display_name(entry)
    return Skill(
        name=name,
        description="",
        dir="",
        markdown="",
        updated_at=0.0,
    )


async def list_skills_cached(
    agent_id: str = Query(...),
    session_id: str | None = Query(
        default=None,
        description=(
            "会话 id。表内已有该智能体记录时**不需要**（查询不碰沙箱）；"
            "缺记录需要回源沙箱列举时必传，否则 422。"
        ),
    ),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> list[Skill]:
    """列出工作区的技能：**先查表，无记录才回源沙箱**（与框架同契约）。

    1. ``agents_extend`` 里**有该 agent_id 的记录** → 直接返回表内容
       （不申请 Pod；``description`` / ``dir`` / ``markdown`` 为空串，
       ``updated_at`` 为 ``0.0``）；
    2. **没有记录**（含仅有其它扩展键、或 ``skills`` 键非法）→ 走原逻辑
       （解析会话 → 路由 Pod → ``workspace.list_skills()``），把结果以
       ``type="unknown"`` 写回表后**原样返回**（沙箱真值，含真实的
       description/dir/markdown）；下次查询即命中表；
    3. 写回失败只打 warning，不影响本次返回。

    Args:
        agent_id (`str`): 智能体 id（query，必传）。
        session_id (`str | None`): 会话 id（query）；仅回源路径需要。
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.
        workspace_manager (`WorkspaceManagerBase`): Injected manager.

    Raises:
        `HTTPException`: 422 if ``session_id`` is missing while the agent has
            no ``agents_extend`` record yet; 404 if the session does not
            exist (from the framework handler).
    """
    cached = await list_agent_skills(storage, agent_id)
    if cached is not None:
        logger.info(
            "list skills: agent %s served from agents_extend (%d skills)",
            agent_id,
            len(cached),
        )
        return [_extend_entry_to_skill(entry) for entry in cached]

    if session_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"session_id is required: agent {agent_id!r} has no "
                "agents_extend record yet, so its skills must be resolved "
                "from the workspace."
            ),
        )

    logger.info(
        "list skills: agent %s has no agents_extend record; resolving from "
        "the sandbox (session=%s)",
        agent_id,
        session_id,
    )
    skills = await _framework_list_skills(
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        storage=storage,
        workspace_manager=workspace_manager,
    )

    try:
        await set_agent_skills(
            storage,
            agent_id,
            [{"type": "unknown", "name": s.name} for s in skills],
            source="sandbox-fallback",
        )
    except Exception:  # noqa: BLE001 —— 写回失败不影响本次返回
        logger.warning(
            "list skills: failed to cache %d skills for agent %s into "
            "agents_extend",
            len(skills),
            agent_id,
            exc_info=True,
        )
    return skills


def install_skill_list_from_table(app: Any) -> None:
    """把 ``GET /workspace/skill`` 前插成"先查表、无记录回源"的版本。

    与 :func:`bocomadp.session_default_mode.install_create_session_default_mode`
    同一手法：往 ``app.router.routes`` **前插**一条同路径同方法的路由
    （Starlette 按顺序取第一个匹配，故生效），框架原路由被遮蔽但保留，
    便于回退/对比。

    **必须在** ``root_app.mount("/api", app)`` **之前调用**（mount 之后
    路由不再挂在同一个 router 上，前插无效）。

    Args:
        app (`Any`):
            FastAPI 应用（``create_app()`` 的返回值，尚未挂到 ``/api``）。
    """
    if getattr(app.state, _SKILL_LIST_INSTALLED_FLAG, False):
        return
    app.router.routes.insert(
        0,
        APIRoute(
            # 与框架 workspace_router 的 prefix="/workspace" + "/skill" 同路径
            "/workspace/skill",
            list_skills_cached,
            methods=["GET"],
            response_model=list[Skill],
            summary=(
                "List installed skills (agents_extend cache; falls back to "
                "the sandbox on first read)"
            ),
            name="list_skills_cached",
        ),
    )
    setattr(app.state, _SKILL_LIST_INSTALLED_FLAG, True)
    logger.info(
        "installed agents_extend-backed GET /workspace/skill",
    )


__all__ = ["install_skill_list_from_table", "skill_router"]
