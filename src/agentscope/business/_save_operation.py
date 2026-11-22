"""Durable receipts for confirmed draft saves in one user/agent registry."""

import hashlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


def _digest(value) -> str:
    data = json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


class SaveOperations:
    """Share claims and receipts across workers using the same registry."""

    def __init__(self, directory: Path):
        self.database = directory / "save-operations.sqlite3"

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def prepare(self, plan, inputs, session_id, reply_id, call_id):
        # Replaying the same approval prompt on another worker yields the
        # same operation. Two separately confirmed identical drafts can save.
        op = _digest([self.database.parent.name, session_id, reply_id, call_id])
        binding = _digest([plan, inputs])
        with self._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS saves (
                operation_id TEXT PRIMARY KEY, binding TEXT NOT NULL,
                target TEXT NOT NULL, tool TEXT NOT NULL, route TEXT NOT NULL,
                state TEXT NOT NULL, receipt_id INTEGER, result TEXT
            )""")
            db.execute("""INSERT INTO saves
                (operation_id, binding, target, tool, route, state)
                VALUES (?, ?, ?, ?, ?, 'prepared')
                ON CONFLICT(operation_id) DO NOTHING""",
                (op, binding, plan["targetLabel"], plan["saveTool"], plan["detailRoute"]),
            )
            if self._row(db, op, plan)["binding"] != binding:
                raise ValueError("草稿或业务定义已变更，请重新发起保存确认。")
        return op

    @staticmethod
    def _row(db, op, plan):
        row = db.execute(
            "SELECT * FROM saves WHERE operation_id=? AND target=? AND tool=?",
            (op, plan["targetLabel"], plan["saveTool"]),
        ).fetchone()
        if row is None:
            raise ValueError("当前用户、账套和工具没有这笔保存记录。")
        return row

    def claim(self, op, plan, inputs):
        if not op or not self.database.exists():
            raise ValueError("请在当前账套重新确认这次保存。")
        with self._transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, op, plan)
            if row["binding"] != _digest([plan, inputs]):
                raise ValueError("草稿或业务定义与确认内容不一致，请重新确认。")
            if row["state"] != "prepared":
                return False
            db.execute("UPDATE saves SET state='submitting' WHERE operation_id=?", (op,))
        # Commit before the first external side effect.
        return True

    def record_receipt(self, op, receipt_id, response=None):
        if type(receipt_id) is not int or receipt_id <= 0:
            raise ValueError("ERP 保存回执缺少有效的单据 ID。")
        metadata = dict(response or {})
        metadata.pop("detail", None)  # Only a verified readback can publish details.
        with self._transaction() as db:
            # A concurrent recovery may already have verified the browser's
            # receipt. Never roll a finished operation back to 'received'.
            db.execute(
                """UPDATE saves SET state='received', receipt_id=?, result=?
                   WHERE operation_id=? AND state='submitting'""",
                (receipt_id, json.dumps(metadata, ensure_ascii=False), op),
            )

    def receipt_slot(self, op):
        """Give a subprocess a one-use slot in the shared registry."""
        directory = self.database.parent / "save-receipts"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (op + ".receipt")
        token = secrets.token_hex(32)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", newline="\n") as slot:
            slot.write(f"agentscope-save-v1 {token}\n")
            slot.flush()
            os.fsync(slot.fileno())
        return {"path": str(path.resolve()), "token": token}

    def finish(self, op, result):
        with self._transaction() as db:
            db.execute(
                "UPDATE saves SET state='finished', result=? WHERE operation_id=?",
                (json.dumps(result, ensure_ascii=False, allow_nan=False), op),
            )

    async def recover(self, op, plan, read_detail):
        if not self.database.exists():
            raise ValueError("当前用户、账套和工具没有这笔保存记录。")
        with self._transaction() as db:
            row = self._row(db, op, plan)
        if row["state"] == "finished":
            return {**json.loads(row["result"]), "operation_id": op}
        if row["state"] == "prepared":
            return {"operation_id": op, "status": "awaiting_confirmation"}
        receipt_id = row["receipt_id"]
        if receipt_id is None:
            # A browser worker can die after recording the POST response but
            # before its JSON result reaches the registry process.
            slot = self.database.parent / "save-receipts" / (op + ".receipt")
            try:
                match = re.fullmatch(rb"agentscope-save-v1 [0-9a-f]{64}\n([1-9][0-9]{0,15})\n",
                                     slot.read_bytes())
            except FileNotFoundError:
                match = None
            if match:
                receipt_id = int(match[1])
                self.record_receipt(op, receipt_id)
        if receipt_id is None:
            return {"operation_id": op, "status": "needs_reconciliation",
                    "message": "提交结果待核对，请核对账套记录后处理。请勿重新建单。"}
        result = json.loads(row["result"] or "{}")
        result.update(operation_id=op, id=receipt_id)
        try:
            detail = await read_detail(row["route"], receipt_id)
            header = detail.get("order", detail) if isinstance(detail, dict) else None
            if not isinstance(header, dict) or header.get("id") != receipt_id:
                raise ValueError("回查结果与已保存单据不符。")
        except Exception:
            return {**result, "status": "saved_needs_readback",
                    "message": "已取得保存回执，请按操作 ID 重试回查。"}
        result.update(status="saved", detail=detail)
        self.finish(op, result)
        return result
