# -*- coding: utf-8 -*-
"""智能体扩展表（``agents_extend``）+ 异步访问层。

与 ``market_store.py`` / ``agent_template_store.py`` 同一套模式：bocomadp
自建 declarative base，复用框架 storage 的 ``_engine`` / ``_session_factory``，
启动时 ``ensure_agents_extend_tables`` 幂等建表，``src/`` 框架零改动。

表语义（**一智能体一行**）：

- ``agent_id``  主键，即 ``agents.id``（1:1，删智能体时级联删行）；
- ``payload``   JSON，智能体的扩展数据。当前结构::

      {
        "skills": [
          {"type": "skillhub", "name": "global:rollback-check-sql"},
          {"type": "uploaded", "name": "我的报表技能"}
        ]
      }

  - ``type == "skillhub"``：``name`` 是完整的技能引用 ``namespace:name``
    （可原样拼回 ``/skill/ensure`` 的入参）；
  - ``type == "uploaded"``：``name`` 是纯名字（用户手动上传的技能，
    建议取 ``SKILL.md`` frontmatter 的 ``name``）；
  - 数组顺序 = 安装顺序；去重键 ``(type, name)``；
  - 顶层按"扩展位"设计：以后可加 ``mcps`` / ``kb`` 等键而不动表结构。

设计动机：原先"某智能体装了哪些技能"只能进沙箱列举（``list_skills`` 走
exec），Pod 被回收/容器重启时该查询就失败；本表把这份清单落到 DB，
查询不必依赖 Pod（写入侧由安装/删除路径写穿，见后续接入）。

列类型约定（与 ``agent_market`` 一致）：需索引的列一律 ``VARCHAR``；
时间戳由应用侧赋值（规避 MySQL 首个 TIMESTAMP 隐式 ``ON UPDATE`` 的坑）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger("bocomadp.agents_extend_store")


# ---------------------------------------------------------------------------
# SQLAlchemy 表（独立 metadata，启动时与框架表一起建）
# ---------------------------------------------------------------------------


class _ExtendBase(DeclarativeBase):
    """bocomadp 专用 declarative base：只服务 agents_extend 表。"""


class AgentsExtendRow(_ExtendBase):
    """一行 = 一个智能体的扩展数据（当前只装已安装技能清单）。"""

    __tablename__ = "agents_extend"

    agent_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    #: 扩展数据（结构见模块 docstring）；MySQL JSON 列无默认值，
    #: 写入方必须始终给一个对象（至少要给 ``{"skills": []}``）。
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)


#: 列级迁移清单：表名 → {列名: DDL}。老库已建过表时 ``create_all`` 只按
#: 表名跳过、不会补新列（ORM 查询全列 SELECT 会报 Unknown column），
#: 故启动时逐列检查、缺列补 ``ALTER TABLE ADD COLUMN``。
#: 当前无迁移项，占位留给后续加字段（例如 skills 之外的扩展键）。
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {"agents_extend": {}}


async def ensure_agents_extend_tables(storage: Any) -> None:
    """启动时建表（幂等，已存在则跳过）+ 列级迁移。

    表不存在 → 按当前模型新建（含主键与 ``payload`` JSON 列）；
    已存在 → 跳过建表，仅按 :data:`_COLUMN_MIGRATIONS` 补缺列。

    Args:
        storage (`Any`):
            框架 storage（提供 ``_engine``）；为 ``None`` 或非 SQL 存储
            （Redis 等）时只打 warning，不影响服务启动。
    """
    engine = getattr(storage, "_engine", None)
    if engine is None:
        logger.warning(
            "storage has no _engine yet; skip agents_extend table "
            "provisioning",
        )
        return
    async with engine.begin() as conn:
        await conn.run_sync(_ExtendBase.metadata.create_all)

    # 复用 market_store 的列级迁移实现（MySQL / SQLite 通用），
    # 避免各 store 各抄一份。
    from bocomadp.market_store import ensure_columns

    await ensure_columns(engine, _COLUMN_MIGRATIONS)
    logger.info("ensured table agents_extend")


# ---------------------------------------------------------------------------
# 业务模型（payload 内条目的校验）
# ---------------------------------------------------------------------------

#: 已知的技能来源类型；``unknown`` 是"只从沙箱列举出来、来源未知"的兜底
#: （例如历史数据或人工放进共享 PVC 的技能）。这里**不做枚举硬校验**，
#: 新增类型不必改代码，坏条目按条跳过即可。
SKILL_TYPE_SKILLHUB = "skillhub"
SKILL_TYPE_UPLOADED = "uploaded"
SKILL_TYPE_UNKNOWN = "unknown"


class SkillEntry(BaseModel):
    """``payload.skills`` 里的一条技能条目。"""

    type: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)

    @field_validator("type", "name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @model_validator(mode="after")
    def _check(self) -> "SkillEntry":
        """形态校验：路径分隔符一律拒绝；``skillhub`` 必须是合法引用。

        - ``skillhub``：复用 ``parse_skill_ref``（``namespace:name``），
          并规范化（strip、namespace 小写）；
        - ``uploaded``：纯名字，**不含冒号**（保证"有冒号 ⇒ skillhub"
          这一判定不歧义）；
        - 其余类型（含 ``unknown``）：只查路径分隔符/``..``，尽量宽松，
          避免沙箱里取回的展示名被误丢。
        """
        if any(bad in self.name for bad in ("/", "\\", "..")):
            raise ValueError(f"unsafe skill name: {self.name!r}")
        if self.type == SKILL_TYPE_SKILLHUB:
            from bocomadp.skills._schema import parse_skill_ref

            namespace, name = parse_skill_ref(self.name)
            self.name = f"{namespace}:{name}"
        elif self.type == SKILL_TYPE_UPLOADED and ":" in self.name:
            raise ValueError(
                f"uploaded skill name must not contain ':': {self.name!r}",
            )
        return self


# ---------------------------------------------------------------------------
# 条目 → 可匹配名（payload 语义，读写两侧共用）
# ---------------------------------------------------------------------------


def entry_display_name(entry: dict) -> str:
    """条目 → agent-facing 名（``skillhub`` 引用取冒号后半段）。

    框架 ``Skill.name`` 与解压目录名都不带命名空间，展示/比对时都用这个；
    其余类型（``uploaded`` / ``unknown``）原样返回。
    """
    name = str(entry.get("name") or "")
    if entry.get("type") == SKILL_TYPE_SKILLHUB and ":" in name:
        return name.split(":", 1)[1]
    return name


def entry_names(entry: dict) -> set[str]:
    """条目的**可匹配名集合**：完整引用 ∪ agent-facing 名（目录名/slug）。

    ``skillhub`` 条目存 ``namespace:slug``（``slug`` 即解压目录名），而框架
    ``Skill.name`` 取 ``SKILL.md`` frontmatter 名 —— 两者可能不同，安装判定
    （``enable_agent_skill`` / ``_ensure_skills`` / 上传清单）**两路都算**，
    否则会出现"明明装了但 ``used`` 仍是 false"。
    """
    raw = str(entry.get("name") or "").strip()
    if not raw:
        return set()
    return {raw, entry_display_name(entry)} - {""}


def _read_entries(payload: Any) -> list[dict[str, str]] | None:
    """payload → **合法**条目列表（去重）；``skills`` 缺失/非列表 → ``None``。

    ``None`` 表示"这份记录不可用"（未记录 / 脏数据），调用方按"没有清单"
    处理 —— 只有手上握着完整清单的调用方（上传路径）才敢覆盖它。
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("skills")
    if not isinstance(raw, list):
        return None
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        try:
            entry = SkillEntry.model_validate(item)
        except ValidationError:
            continue
        key = (entry.type, entry.name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": entry.type, "name": entry.name})
    return out


def _listed_name_to_entry(name: str) -> dict[str, str] | None:
    """沙箱清单里的名字 → 表条目：优先 ``uploaded``，名字不合法再退 ``unknown``。"""
    for entry_type in (SKILL_TYPE_UPLOADED, SKILL_TYPE_UNKNOWN):
        try:
            entry = SkillEntry.model_validate(
                {"type": entry_type, "name": name},
            )
        except ValidationError:
            continue
        return {"type": entry.type, "name": entry.name}
    return None


# ---------------------------------------------------------------------------
# 异步访问层（storage 参数就是框架 storage，含 _session_factory）
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """DateTime() 无时区列统一用 UTC naive 时间（与 market_store 一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _session_factory(storage: Any) -> Any:
    return getattr(storage, "_session_factory", None)


async def get_extend_payload(storage: Any, agent_id: str) -> dict | None:
    """取该智能体的扩展 payload；**无记录返回 ``None``**。

    调用方据此区分"没查过"（``None``，可以回源沙箱）与"查过但没有技能"
    （``{"skills": []}``，直接返回空列表、不必再进沙箱）。
    """
    factory = _session_factory(storage)
    if factory is None:
        return None
    async with factory() as session:
        row = await session.get(AgentsExtendRow, agent_id)
        if row is None:
            return None
        payload = row.payload
        return dict(payload) if isinstance(payload, dict) else {}


async def list_agent_skills(
    storage: Any,
    agent_id: str,
) -> list[dict[str, str]] | None:
    """该智能体已记录的技能清单；**无记录返回 ``None``**。

    - 无行 → ``None``（调用方回源沙箱并把结果写回）；
    - 有行但 ``skills`` 键缺失/非法 → 也返回 ``None``（按"没查过"处理，
      回源重建，自愈脏数据）；
    - 有条目但个别条目非法 → **跳过该条目**并打 warning，其余照常返回。
    """
    payload = await get_extend_payload(storage, agent_id)
    if payload is None:
        return None
    raw = payload.get("skills")
    if not isinstance(raw, list):
        logger.warning(
            "agents_extend: agent %s has no valid 'skills' list "
            "(payload=%r); treating as uncached",
            agent_id,
            payload,
        )
        return None

    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        try:
            entry = SkillEntry.model_validate(item)
        except ValidationError:
            logger.warning(
                "agents_extend: agent %s has an invalid skill entry %r; "
                "skipped",
                agent_id,
                item,
            )
            continue
        key = (entry.type, entry.name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": entry.type, "name": entry.name})
    return out


async def set_agent_skills(
    storage: Any,
    agent_id: str,
    entries: list[dict[str, str]],
    *,
    source: str = "",
) -> None:
    """**全量覆盖**该智能体的技能清单（upsert 一行）。

    - 条目按 ``(type, name)`` 去重，非法条目跳过（不抛）；
    - payload 顶层其余扩展键（将来的 ``mcps`` 等）**原样保留**；
    - ``payload`` 整体替换（不是原地改字典）——MySQL JSON 列的原地
      改动不会被 SQLAlchemy 侦测到，必须换新对象才会落库；
    - ``created_at`` 首次写入时赋值，之后不变。
    """
    factory = _session_factory(storage)
    if factory is None:
        return

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in entries:
        try:
            entry = SkillEntry.model_validate(item)
        except ValidationError:
            logger.warning(
                "agents_extend: skip invalid skill entry %r for agent %s",
                item,
                agent_id,
            )
            continue
        key = (entry.type, entry.name)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"type": entry.type, "name": entry.name})

    now = _now()
    async with factory() as session:
        row = await session.get(AgentsExtendRow, agent_id)
        if row is None:
            session.add(
                AgentsExtendRow(
                    agent_id=agent_id,
                    payload={"skills": normalized},
                    created_at=now,
                    updated_at=now,
                ),
            )
        else:
            payload = dict(row.payload or {})
            payload["skills"] = normalized
            row.payload = payload
            row.updated_at = now
        await session.commit()
    logger.info(
        "agents_extend: agent %s skills updated to %d entries (source=%s)",
        agent_id,
        len(normalized),
        source or "-",
    )


async def ensure_agent_extend_row(
    storage: Any,
    agent_id: str,
    *,
    source: str = "",
) -> bool:
    """确保该智能体有一行扩展记录，且 ``payload.skills`` 为空数组。

    **只在没有行时插入**（``payload = {"skills": []}``）：已有行原样保留
    （返回 ``False``，不覆盖/不清空既有技能清单）。用于"创建智能体时预置
    一条空记录"——新智能体必然没有已装技能，先落一行让
    ``GET /workspace/skill`` 直接命中表，不必解析会话 → 路由 Pod →
    ``exec`` 列举（Pod 被回收 / 容器重启时那条路径会失败）。

    Args:
        storage (`Any`):
            框架 storage（含 ``_session_factory``）；非 SQL 存储返回
            ``False``。
        agent_id (`str`): 智能体 id（= ``agents.id``）。
        source (`str`): 仅用于日志的写入来源标记。

    Returns:
        `bool`: 插入了新行返回 ``True``；已有行（未改动）返回 ``False``。
    """
    factory = _session_factory(storage)
    if factory is None:
        return False

    now = _now()
    async with factory() as session:
        row = await session.get(AgentsExtendRow, agent_id)
        if row is not None:
            return False
        session.add(
            AgentsExtendRow(
                agent_id=agent_id,
                payload={"skills": []},
                created_at=now,
                updated_at=now,
            ),
        )
        await session.commit()
    logger.info(
        "agents_extend: ensured empty row for agent %s (source=%s)",
        agent_id,
        source or "-",
    )
    return True


async def delete_agent_extend(storage: Any, agent_id: str) -> bool:
    """删除该智能体的整行扩展记录（删智能体时级联）。

    删到返回 ``True``，本来就没有行（或非 SQL 存储）返回 ``False``。
    单智能体一行且无外键，直接删行即可。
    """
    factory = _session_factory(storage)
    if factory is None:
        return False
    async with factory() as session:
        row = await session.get(AgentsExtendRow, agent_id)
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
    logger.info("agents_extend: deleted row for agent %s", agent_id)
    return True


async def append_extend_entry(
    storage: Any,
    agent_id: str,
    entry: dict[str, str],
    *,
    source: str = "",
    create_if_missing: bool = False,
) -> bool:
    """向该智能体的 ``payload.skills`` 追加一条（读-改-写，按 ``(type,name)`` 去重）。

    - 表里有该 agent 的行 → 追加（已存在则不动，返回 ``False``）；
    - **没有行**：``create_if_missing=False``（默认）不动表并返回 ``False``
      —— 单条追加没有"该 agent 还装了别的什么"的信息，写一条会被读侧当成
      全量清单；只有手上握着**完整清单**的调用方（如上传路径）才传 ``True``；
    - 行存在但 ``skills`` 键缺失/非法 → 视为"记录不可用"，**不动**（返回
      ``False``），留给有完整清单的路径去覆盖；
    - ``payload`` 顶层其它扩展键原样保留；条目非法（``SkillEntry`` 校验不过）
      跳过并打 warning。

    Returns:
        `bool`: 表被改动（新增条目 / 建行）返回 ``True``。

    Raises:
        底层 DB 异常原样抛出（是否降级由调用方决定）。
    """
    factory = _session_factory(storage)
    if factory is None:
        return False
    try:
        parsed = SkillEntry.model_validate(entry)
    except ValidationError:
        logger.warning(
            "agents_extend: invalid entry %r for agent %s; skipped",
            entry,
            agent_id,
        )
        return False
    item = {"type": parsed.type, "name": parsed.name}

    now = _now()
    async with factory() as session:
        row = await session.get(AgentsExtendRow, agent_id)
        if row is None:
            if not create_if_missing:
                logger.info(
                    "agents_extend: agent %s has no record; skip appending "
                    "%s:%s (source=%s)",
                    agent_id,
                    item["type"],
                    item["name"],
                    source or "-",
                )
                return False
            session.add(
                AgentsExtendRow(
                    agent_id=agent_id,
                    payload={"skills": [item]},
                    created_at=now,
                    updated_at=now,
                ),
            )
        else:
            entries = _read_entries(row.payload)
            if entries is None:
                logger.warning(
                    "agents_extend: agent %s has an unusable 'skills' list; "
                    "skip appending %s:%s",
                    agent_id,
                    item["type"],
                    item["name"],
                )
                return False
            if any(
                e["type"] == item["type"] and e["name"] == item["name"]
                for e in entries
            ):
                return False
            payload = dict(row.payload or {})
            payload["skills"] = [*entries, item]
            row.payload = payload
            row.updated_at = now
        await session.commit()
    logger.info(
        "agents_extend: agent %s += %s:%s (source=%s)",
        agent_id,
        item["type"],
        item["name"],
        source or "-",
    )
    return True


async def remove_extend_entries(
    storage: Any,
    agent_id: str,
    name: str,
    *,
    source: str = "",
) -> bool:
    """删掉与 ``name`` 匹配的技能条目（卸载 / 删除智能体时用）。

    匹配口径 = :func:`entry_names`（完整引用 ∪ agent-facing 名/目录名），
    因此前端传 frontmatter 名、目录名或 ``namespace:name`` 都能删中。
    没有行 / 记录不可用 / 没有条目匹配 → 不动表，返回 ``False``。

    Returns:
        `bool`: 表被改动返回 ``True``。

    Raises:
        底层 DB 异常原样抛出。
    """
    factory = _session_factory(storage)
    if factory is None:
        return False
    target = (name or "").strip()
    if not target:
        return False

    now = _now()
    async with factory() as session:
        row = await session.get(AgentsExtendRow, agent_id)
        if row is None:
            return False
        entries = _read_entries(row.payload)
        if entries is None:
            return False
        kept = [e for e in entries if target not in entry_names(e)]
        if len(kept) == len(entries):
            return False
        payload = dict(row.payload or {})
        payload["skills"] = kept
        row.payload = payload
        row.updated_at = now
        await session.commit()
    logger.info(
        "agents_extend: agent %s -= %s (%d entries left, source=%s)",
        agent_id,
        target,
        len(kept),
        source or "-",
    )
    return True


async def sync_extend_from_names(
    storage: Any,
    agent_id: str,
    names: list[str],
    *,
    source: str = "",
    create_if_missing: bool = True,
) -> bool:
    """用一份**完整清单**（沙箱 ``list_skills()`` 结果）补齐扩展记录。

    与 :func:`append_extend_entry` 的区别在于"信息完整性"：这里拿得到该
    agent 当前**全部**技能，所以可以安全地覆盖不可用的记录、甚至在**没有行
    时建行**（上传路径即如此：建出来的行按约定一律写 ``uploaded``）。

    - 已有行：**只追加清单里缺的**（按 :func:`entry_names` 判"已有"），
      已有的 ``skillhub`` 引用不被动、不丢；
    - 已有行但 ``skills`` 不可用：按本次清单**整份重建**（自愈脏数据）；
    - 没有行：``create_if_missing=True`` 且清单非空 → 建行，否则不动；
    - 名字非法（``uploaded`` 名不能含 ``:``）→ 退 ``unknown``；两者都不合法
      → 跳过该名字并打 warning。

    Returns:
        `bool`: 表被改动返回 ``True``。

    Raises:
        底层 DB 异常原样抛出。
    """
    factory = _session_factory(storage)
    if factory is None:
        return False

    wanted: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = (raw or "").strip()
        if name and name not in seen:
            seen.add(name)
            wanted.append(name)
    if not wanted:
        return False

    now = _now()
    async with factory() as session:
        row = await session.get(AgentsExtendRow, agent_id)
        if row is None and not create_if_missing:
            return False

        # 没有行 → 从空开始；有行但记录不可用（脏数据）→ 也当空、按本次
        # 完整清单整份重建；记录可用 → 在原条目上增量补齐（保住 skillhub 引用）。
        entries: list[dict[str, str]] = list(
            (_read_entries(row.payload) if row is not None else None) or [],
        )
        covered: set[str] = set()
        for entry in entries:
            covered |= entry_names(entry)

        added = 0
        for name in wanted:
            if name in covered:
                continue
            item = _listed_name_to_entry(name)
            if item is None:
                logger.warning(
                    "agents_extend: listed name %r of agent %s is not a "
                    "valid entry; skipped",
                    name,
                    agent_id,
                )
                continue
            entries.append(item)
            covered |= entry_names(item)
            added += 1

        if row is not None and added == 0:
            return False
        if not entries:
            return False
        if row is None:
            session.add(
                AgentsExtendRow(
                    agent_id=agent_id,
                    payload={"skills": entries},
                    created_at=now,
                    updated_at=now,
                ),
            )
        else:
            payload = dict(row.payload or {})
            payload["skills"] = entries
            row.payload = payload
            row.updated_at = now
        await session.commit()
    logger.info(
        "agents_extend: agent %s synced from listing (+%d entries, "
        "total=%d, source=%s)",
        agent_id,
        added,
        len(entries),
        source or "-",
    )
    return True


__all__ = [
    "AgentsExtendRow",
    "SKILL_TYPE_SKILLHUB",
    "SKILL_TYPE_UNKNOWN",
    "SKILL_TYPE_UPLOADED",
    "SkillEntry",
    "append_extend_entry",
    "delete_agent_extend",
    "ensure_agent_extend_row",
    "ensure_agents_extend_tables",
    "entry_display_name",
    "entry_names",
    "get_extend_payload",
    "list_agent_skills",
    "remove_extend_entries",
    "set_agent_skills",
    "sync_extend_from_names",
]
