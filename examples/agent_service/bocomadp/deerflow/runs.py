# -*- coding: utf-8 -*-
"""RunManager：run_id 生成、session↔run 映射、状态推断（内存表）。

对齐 deer-flow 2.0 ``RunManager``/``RunRecord`` 的职责，但**只做进程内
轻量记账**：

- 并发控制不重复实现——进程内 409 复用原生 ``ChatRunRegistry.spawn``
  （同 session 至多一个活跃 task），跨进程由原生分布式锁保证。
- run 状态为**内存推断**（pending → running → success/error/interrupted），
  从事件流/后台任务完成事件推导，不逐事件持久化；多副本部署时仅
  ``session↔run`` 映射需换 Redis（接口不变，见方案裁剪项 3）。
- 记录在 run 结束后延迟清理（默认 300s），给晚到 join 方留足回放窗口。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from agentscope._utils._common import _generate_id

if TYPE_CHECKING:
    from agentscope.app._manager._chat_run_registry import ChatRunRegistry

logger = logging.getLogger(__name__)


class RunStatus(StrEnum):
    """Run 状态（对齐 deer-flow manager.py 状态机子集）。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass
class RunRecord:
    """单条 run 的记账记录（进程内）。"""

    run_id: str
    """全局唯一 run 标识（Content-Location 与事件 run_id 字段同源）。"""
    session_id: str
    """所属 thread（== 原生 session id）。"""
    user_id: str
    """发起用户。"""
    agent_id: str
    """目标 agent。"""
    status: RunStatus = RunStatus.PENDING
    """推断状态。"""
    created_at: float = field(default_factory=time.monotonic)
    """创建时间（单调时钟）。"""
    finished_at: float | None = None
    """结束时间；``None`` 表示仍活跃。"""
    error: str | None = None
    """失败时的错误摘要。"""

    @property
    def active(self) -> bool:
        """是否仍活跃（未结束）。"""
        return self.finished_at is None


class RunManager:
    """进程内 run 记账与 session↔run 映射。"""

    def __init__(self, *, cleanup_delay: float = 300.0) -> None:
        """Args:
            cleanup_delay (`float`, optional):
                已结束 run 的保留时长（秒），之后被惰性清理。

        Note:
            原生 ``ChatRunRegistry`` 是 lifespan 单例（请求时才可从
            ``app.state`` 取），因此不在此构造注入；
            :meth:`create_or_reject` 每次调用显式传入（见路由层）。
        """
        self._cleanup_delay = cleanup_delay
        self._records: dict[str, RunRecord] = {}
        self._by_session: dict[str, str] = {}

    # ── 创建 ──────────────────────────────────────────────────────────

    def create_or_reject(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        *,
        native_registry: "ChatRunRegistry | None" = None,
    ) -> RunRecord:
        """为该 session 创建 run；session 已有活跃 run 时拒绝。

        活跃判定两个来源：本管理器内存表（本次服务创建的 run）+ 原生
        ``ChatRunRegistry``（任何入口创建的 run，含 `/chat/` 触发的，
        由调用方传入）。

        Args:
            native_registry (`ChatRunRegistry | None`, optional):
                原生运行注册表（路由层从 ``app.state`` 注入）。提供时
                一并检查原生链路是否占用 session。

        Raises:
            `RuntimeError`:
                session 已有活跃 run（路由层翻译为 409 Conflict）。
        """
        self._cleanup_locked()
        existing_id = self._by_session.get(session_id)
        if existing_id is not None:
            existing = self._records.get(existing_id)
            if existing is not None and existing.active:
                raise RuntimeError(
                    f"Session {session_id!r} already has an active run "
                    f"{existing_id!r}.",
                )
        if native_registry is not None:
            task = native_registry.get(session_id)
            if task is not None and not task.done():
                raise RuntimeError(
                    f"Session {session_id!r} is already running via the "
                    "native chat path.",
                )

        record = RunRecord(
            run_id=_generate_id(),
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
        )
        self._records[record.run_id] = record
        self._by_session[session_id] = record.run_id
        logger.info(
            "RunManager: created run %s for session %s (agent=%s).",
            record.run_id,
            session_id,
            agent_id,
        )
        return record

    # ── 查询 ──────────────────────────────────────────────────────────

    def get(self, run_id: str) -> RunRecord | None:
        """按 run_id 查询记录。"""
        self._cleanup_locked()
        return self._records.get(run_id)

    def get_by_session(self, session_id: str) -> RunRecord | None:
        """查询 session 当前映射的 run（可能已结束）。"""
        self._cleanup_locked()
        run_id = self._by_session.get(session_id)
        if run_id is None:
            return None
        return self._records.get(run_id)

    # ── 状态更新 ──────────────────────────────────────────────────────

    def set_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: str | None = None,
    ) -> RunRecord | None:
        """更新 run 状态；结束态同时记录 finished_at。

        Returns:
            `RunRecord | None`: 更新后的记录；run 不存在时 ``None``。
        """
        record = self._records.get(run_id)
        if record is None:
            return None
        record.status = status
        record.error = error
        if status in (RunStatus.SUCCESS, RunStatus.ERROR, RunStatus.INTERRUPTED):
            record.finished_at = time.monotonic()
        return record

    def mark_finished(
        self,
        run_id: str,
        status: RunStatus = RunStatus.SUCCESS,
        *,
        error: str | None = None,
    ) -> None:
        """run 结束回调（后台任务 done 时调用）。

        已置为 ``INTERRUPTED`` 的 run（路由层 cancel 先行落定）不接受
        done 回调覆盖——cancel 是用户显式意图，优先于任务自然结束。
        """
        record = self._records.get(run_id)
        if record is None:
            return
        if record.status == RunStatus.INTERRUPTED and status != RunStatus.INTERRUPTED:
            logger.info(
                "RunManager: run %s already interrupted; "
                "ignore done callback as %s.",
                run_id,
                status.value,
            )
            return
        self.set_status(run_id, status, error=error)
        logger.info(
            "RunManager: run %s finished as %s.",
            run_id,
            status.value,
        )

    # ── 清理 ──────────────────────────────────────────────────────────

    def cleanup(self) -> int:
        """删除结束超过 ``cleanup_delay`` 的记录，返回清理条数。"""
        now = time.monotonic()
        stale = [
            run_id
            for run_id, rec in self._records.items()
            if rec.finished_at is not None
            and now - rec.finished_at >= self._cleanup_delay
        ]
        for run_id in stale:
            rec = self._records.pop(run_id, None)
            if rec is not None and self._by_session.get(rec.session_id) == run_id:
                self._by_session.pop(rec.session_id, None)
        if stale:
            logger.info(
                "RunManager: cleaned up %d finished run(s).",
                len(stale),
            )
        return len(stale)

    def _cleanup_locked(self) -> None:
        """查询路径上的惰性清理（无锁；单进程内使用足够）。"""
        try:
            self.cleanup()
        except Exception:  # noqa: BLE001 —— 清理失败不影响主流程
            logger.exception("RunManager: lazy cleanup failed.")


__all__ = ["RunManager", "RunRecord", "RunStatus"]
