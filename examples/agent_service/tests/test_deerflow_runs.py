"""RunManager 单测（runs.py）。

覆盖：create_or_reject 冲突拒绝、状态机迁移、延迟清理、session↔run
映射。409 语义与原生 ChatRunRegistry 的联动在路由层测试（见 test.sh
集成项）。
"""

from __future__ import annotations

import time

import pytest

from bocomadp.deerflow.runs import RunManager, RunStatus


def test_create_and_get() -> None:
    mgr = RunManager()
    rec = mgr.create_or_reject("u1", "s1", "a1")
    assert rec.run_id
    assert rec.session_id == "s1"
    assert rec.user_id == "u1"
    assert rec.agent_id == "a1"
    assert rec.status == RunStatus.PENDING
    assert rec.active
    assert mgr.get(rec.run_id) is rec
    assert mgr.get_by_session("s1") is rec


def test_create_reject_while_active() -> None:
    mgr = RunManager()
    mgr.create_or_reject("u1", "s1", "a1")
    with pytest.raises(RuntimeError):
        mgr.create_or_reject("u1", "s1", "a1")


def test_create_after_finish() -> None:
    mgr = RunManager()
    rec = mgr.create_or_reject("u1", "s1", "a1")
    mgr.mark_finished(rec.run_id, RunStatus.SUCCESS)
    rec2 = mgr.create_or_reject("u1", "s1", "a1")
    assert rec2.run_id != rec.run_id
    # 同 session 新 run 覆盖映射
    assert mgr.get_by_session("s1") is rec2


def test_status_transitions() -> None:
    mgr = RunManager()
    rec = mgr.create_or_reject("u1", "s1", "a1")
    assert mgr.set_status(rec.run_id, RunStatus.RUNNING) is rec
    assert rec.status == RunStatus.RUNNING
    assert rec.active
    mgr.mark_finished(rec.run_id, RunStatus.ERROR, error="boom")
    assert rec.status == RunStatus.ERROR
    assert rec.error == "boom"
    assert not rec.active
    assert rec.finished_at is not None


def test_mark_finished_interrupted_not_overwritten_by_done_callback() -> None:
    """路由层 cancel 置 interrupted 后，后台任务 done 回调不覆盖。"""
    mgr = RunManager()
    rec = mgr.create_or_reject("u1", "s1", "a1")
    mgr.mark_finished(rec.run_id, RunStatus.INTERRUPTED)
    mgr.mark_finished(rec.run_id, RunStatus.SUCCESS)  # 模拟 done 回调
    assert rec.status == RunStatus.INTERRUPTED


def test_set_status_unknown_run_returns_none() -> None:
    mgr = RunManager()
    assert mgr.set_status("nope", RunStatus.SUCCESS) is None


def test_cleanup_removes_finished_records() -> None:
    mgr = RunManager(cleanup_delay=0.01)
    rec = mgr.create_or_reject("u1", "s1", "a1")
    mgr.mark_finished(rec.run_id, RunStatus.SUCCESS)
    time.sleep(0.02)
    assert mgr.cleanup() >= 1
    assert mgr.get(rec.run_id) is None
    assert mgr.get_by_session("s1") is None


def test_cleanup_keeps_active_records() -> None:
    mgr = RunManager(cleanup_delay=0.01)
    rec = mgr.create_or_reject("u1", "s1", "a1")
    time.sleep(0.02)
    assert mgr.cleanup() == 0
    assert mgr.get(rec.run_id) is rec


def test_lazy_cleanup_on_query() -> None:
    mgr = RunManager(cleanup_delay=0.01)
    rec = mgr.create_or_reject("u1", "s1", "a1")
    mgr.mark_finished(rec.run_id, RunStatus.SUCCESS)
    time.sleep(0.02)
    # 查询路径触发惰性清理
    assert mgr.get(rec.run_id) is None
    assert mgr.get_by_session("s1") is None
