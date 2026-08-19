# -*- coding: utf-8 -*-
"""上传文件元数据持久化（SQLite）。

仅存储**元数据**（虚拟路径、文件名、大小、转换结果、大纲文本等），
原始文件与 ``.md`` 正文均位于沙箱 workdir 内（或本地模式回退路径），
由 ``UploadProvider`` 通过 backend 读写。

``UploadsMiddleware`` 是同步 ASGI 中间件，无法在沙箱内执行 backend IO，
因此在上传时即把 ``.md`` 大纲写入 ``UploadedFile.markdown`` 列，
中间件渲染 ``<context name="files">`` 时直接读取该列，无需触碰沙箱。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from bocomadp.config.base import BASE_DIR


class UploadedFileCreate(BaseModel):
    """上传记录创建入参。"""

    user_id: str
    agent_id: str
    session_id: str
    workspace_id: Optional[str] = None
    original_name: str
    stored_name: str
    virtual_path: str
    size_bytes: int
    content_type: Optional[str] = None
    converted: bool = False
    convert_format: Optional[str] = None
    convert_error: Optional[str] = None
    markdown: Optional[str] = None
    base64: Optional[str] = None
    mime_type: Optional[str] = None


class UploadedFile(BaseModel):
    """上传文件元数据（与 SQLite 表 1:1）。"""

    id: int
    user_id: str
    agent_id: str
    session_id: str
    workspace_id: Optional[str] = None
    original_name: str
    stored_name: str
    virtual_path: str
    size_bytes: int
    content_type: Optional[str] = None
    converted: bool = False
    convert_format: Optional[str] = None
    convert_error: Optional[str] = None
    markdown: Optional[str] = None
    base64: Optional[str] = None
    mime_type: Optional[str] = None
    created_at: str

    model_config = {"from_attributes": True}

    @property
    def is_image(self) -> bool:
        """是否为图片上传（上传时已固化为 base64）。"""
        return bool(self.base64) and bool(self.mime_type)


_DB_PATH = BASE_DIR / "data" / "uploads.db"


class UploadsDB:
    """线程安全的 SQLite 封装（同步，供路由与中间件共用）。"""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS uploaded_files (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       TEXT NOT NULL,
                agent_id      TEXT NOT NULL,
                session_id    TEXT NOT NULL,
                workspace_id  TEXT,
                original_name TEXT NOT NULL,
                stored_name   TEXT NOT NULL,
                virtual_path  TEXT NOT NULL,
                size_bytes    INTEGER NOT NULL,
                content_type  TEXT,
                converted     INTEGER NOT NULL DEFAULT 0,
                convert_format TEXT,
                convert_error TEXT,
                markdown      TEXT,
                base64        TEXT,
                mime_type     TEXT,
                created_at    TEXT NOT NULL
            )
            """,
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_uploads_session "
            "ON uploaded_files (user_id, agent_id, session_id)",
        )
        self._ensure_columns()
        self._conn.commit()

    def _ensure_columns(self) -> None:
        """轻量迁移：旧库缺 base64 / mime_type 列时补列（幂等）。"""
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(uploaded_files)")
        }
        for column, ddl in (
            ("base64", "ALTER TABLE uploaded_files ADD COLUMN base64 TEXT"),
            ("mime_type", "ALTER TABLE uploaded_files ADD COLUMN mime_type TEXT"),
        ):
            if column not in existing:
                self._conn.execute(ddl)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def add(self, data: UploadedFileCreate) -> UploadedFile:
        cur = self._conn.execute(
            """
            INSERT INTO uploaded_files (
                user_id, agent_id, session_id, workspace_id, original_name,
                stored_name, virtual_path, size_bytes, content_type, converted,
                convert_format, convert_error, markdown, base64, mime_type,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.user_id,
                data.agent_id,
                data.session_id,
                data.workspace_id,
                data.original_name,
                data.stored_name,
                data.virtual_path,
                data.size_bytes,
                data.content_type,
                1 if data.converted else 0,
                data.convert_format,
                data.convert_error,
                data.markdown,
                data.base64,
                data.mime_type,
                self._now(),
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM uploaded_files WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return UploadedFile(**dict(row))  # type: ignore[arg-type]

    def list_by_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> list[UploadedFile]:
        """列举某会话的上传记录。

        方案 A 下虚拟路径不再编码 agent，文件隔离由工作区（workdir）
        物理保证，因此 ``agent_id`` 仅作记录字段而**不是**强制过滤条件：
        当调用方传入空 ``agent_id``（如 builtin 工具按 user/session 列举）
        时，仅按 ``(user_id, session_id)`` 过滤，可正确返回该会话全部
        上传；传入真实 ``agent_id`` 时（如 ``GET /files/uploads``）则
        额外按 agent 精确过滤。
        """
        if agent_id:
            rows = self._conn.execute(
                """
                SELECT * FROM uploaded_files
                WHERE user_id = ? AND agent_id = ? AND session_id = ?
                ORDER BY id DESC
                """,
                (user_id, agent_id, session_id),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM uploaded_files
                WHERE user_id = ? AND session_id = ?
                ORDER BY id DESC
                """,
                (user_id, session_id),
            ).fetchall()
        return [UploadedFile(**dict(r)) for r in rows]  # type: ignore[arg-type]

    def get_by_virtual_path(self, virtual_path: str) -> UploadedFile | None:
        row = self._conn.execute(
            "SELECT * FROM uploaded_files WHERE virtual_path = ? LIMIT 1",
            (virtual_path,),
        ).fetchone()
        return UploadedFile(**dict(row)) if row else None  # type: ignore[arg-type]

    def get_by_session_file(
        self,
        user_id: str,
        session_id: str,
        stored_name: str,
        agent_id: str = "",
    ) -> UploadedFile | None:
        """按 ``(user_id, session_id, stored_name)`` 定位单条记录。

        方案 A 下虚拟路径不再编码 session，无法仅凭 virtual_path 唯一确定记录，
        故下载 / 删除 / 读取均走本方法（调用方已有 user_id / session_id）。

        ``agent_id`` 默认空串：传入时额外按 agent 精确过滤，避免同一 user/session
        下不同 agent 上传同名文件时 ``LIMIT 1`` 误命中其它 agent 的记录（方案 A
        中不同 agent 拥有独立 workdir 物理隔离，但 SQLite 元数据共用同一库）。
        """
        if agent_id:
            row = self._conn.execute(
                """
                SELECT * FROM uploaded_files
                WHERE user_id = ? AND session_id = ? AND agent_id = ?
                  AND stored_name = ?
                LIMIT 1
                """,
                (user_id, session_id, agent_id, stored_name),
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT * FROM uploaded_files
                WHERE user_id = ? AND session_id = ? AND stored_name = ?
                LIMIT 1
                """,
                (user_id, session_id, stored_name),
            ).fetchone()
        return UploadedFile(**dict(row)) if row else None  # type: ignore[arg-type]

    def count_by_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> int:
        """统计某会话上传文件数（过滤语义同 ``list_by_session``）。"""
        if agent_id:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS c FROM uploaded_files
                WHERE user_id = ? AND agent_id = ? AND session_id = ?
                """,
                (user_id, agent_id, session_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS c FROM uploaded_files
                WHERE user_id = ? AND session_id = ?
                """,
                (user_id, session_id),
            ).fetchone()
        return int(row["c"]) if row else 0

    def delete(self, user_id: str, agent_id: str, session_id: str, stored_name: str) -> bool:
        cur = self._conn.execute(
            """
            DELETE FROM uploaded_files
            WHERE user_id = ? AND agent_id = ? AND session_id = ?
              AND stored_name = ?
            """,
            (user_id, agent_id, session_id, stored_name),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_by_session(self, user_id: str, agent_id: str, session_id: str) -> int:
        """删除某会话的全部上传元数据（会话清理时调用）。"""
        cur = self._conn.execute(
            """
            DELETE FROM uploaded_files
            WHERE user_id = ? AND agent_id = ? AND session_id = ?
            """,
            (user_id, agent_id, session_id),
        )
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()


# 模块级单例（与 storage 生命周期一致）
_uploads_db: UploadsDB | None = None


def get_uploads_db() -> UploadsDB:
    global _uploads_db
    if _uploads_db is None:
        _uploads_db = UploadsDB()
    return _uploads_db
