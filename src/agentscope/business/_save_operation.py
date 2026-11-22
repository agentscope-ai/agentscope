"""Durable receipts for confirmed draft saves in one user/agent registry."""

import hashlib
import json
import os
import re
import secrets
import sqlite3
from collections.abc import Callable
from contextlib import closing, contextmanager
from pathlib import Path


ReceiptId = int | str
# The adapter projects a system's detail into the names and units the user confirmed.
ReadbackView = Callable[[dict, dict], tuple[ReceiptId, dict]]


def valid_receipt(value) -> bool:
    return (type(value) is int and value > 0) or (isinstance(value, str) and bool(value.strip()))


def write_save_receipt(slot: dict, receipt_id: ReceiptId) -> None:
    """Persist the upstream ID before a connector waits for any further I/O."""
    if not valid_receipt(receipt_id):
        raise ValueError("保存回执缺少有效的记录 ID。")
    header = f"agentscope-save-v1 {slot['token']}\n".encode("ascii")
    payload = json.dumps(receipt_id).encode("ascii") + b"\n"
    with open(slot["path"], "r+b") as stream:
        current = stream.read()
        if current == header + payload:
            return
        if current != header:
            raise ValueError("保存回执与当前操作不符。")
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _matches(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and not differing_fields(expected, actual)
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(_matches(x, y) for x, y in zip(expected, actual)))
    # JSON booleans and numbers are distinct even though Python equates True and 1.
    return isinstance(expected, bool) == isinstance(actual, bool) and expected == actual


def differing_fields(expected, actual):
    """Compare the confirmed fields, allowing the system to add metadata."""
    return [key for key, value in expected.items()
            if key not in actual or not _matches(value, actual[key])]


def readback_fields(view, plan, detail, receipt_id):
    key, fields = view(plan, detail)
    if not valid_receipt(key) or key != receipt_id or not isinstance(fields, dict):
        raise ValueError("回查记录与保存回执不符。")
    return fields


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
                state TEXT NOT NULL, receipt_id INTEGER, result TEXT, confirmed_inputs TEXT
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
            raise ValueError("当前用户、目标系统和工具没有这笔保存记录。")
        return row

    def claim(self, op, plan, inputs):
        if not op or not self.database.exists():
            raise ValueError("请在当前系统重新确认这次保存。")
        with self._transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, op, plan)
            if row["binding"] != _digest([plan, inputs]):
                raise ValueError("草稿或业务定义与确认内容不一致，请重新确认。")
            if row["state"] != "prepared":
                return False
            if "confirmed_inputs" not in row.keys():
                db.execute("ALTER TABLE saves ADD COLUMN confirmed_inputs TEXT")
            db.execute(
                "UPDATE saves SET state='submitting', confirmed_inputs=? WHERE operation_id=?",
                (json.dumps(inputs, ensure_ascii=False, allow_nan=False), op),
            )
        # Commit before the first external side effect.
        return True

    def record_receipt(self, op, receipt_id, response=None):
        if not valid_receipt(receipt_id):
            raise ValueError("保存回执缺少有效的记录 ID。")
        metadata = dict(response or {})
        metadata.pop("detail", None)  # Only a verified readback can publish details.
        # Keep existing integer receipts readable by older workers. Other keys must
        # avoid SQLite's INTEGER affinity, which would strip leading zeros.
        stored_id = receipt_id
        if isinstance(receipt_id, str) or receipt_id > 2**63 - 1:
            stored_id = "json:" + json.dumps(receipt_id)
        with self._transaction() as db:
            # A concurrent recovery may already have verified the browser's
            # receipt. Never roll a finished operation back to 'received'.
            db.execute(
                """UPDATE saves SET state='received', receipt_id=?, result=?
                   WHERE operation_id=? AND state='submitting'""",
                (stored_id, json.dumps(metadata, ensure_ascii=False), op),
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
                "UPDATE saves SET state='finished', result=? WHERE operation_id=? AND state!='finished'",
                (json.dumps(result, ensure_ascii=False, allow_nan=False), op),
            )

    def _recover_receipt(self, op):
        # A connector can exit after persisting the ID but before replying.
        slot = self.database.parent / "save-receipts" / (op + ".receipt")
        try:
            match = re.fullmatch(rb"agentscope-save-v1 [0-9a-f]{64}\n([^\n]+)\n", slot.read_bytes())
            if match:
                receipt_id = json.loads(match[1])
                if valid_receipt(receipt_id):
                    self.record_receipt(op, receipt_id)
                    return receipt_id
        except (FileNotFoundError, ValueError):
            pass
        return None

    async def recover(self, op, plan, read_detail, readback_view: ReadbackView, inline_detail=None):
        if not self.database.exists():
            raise ValueError("当前用户、目标系统和工具没有这笔保存记录。")
        with self._transaction() as db:
            row = self._row(db, op, plan)
        snapshot = row["confirmed_inputs"] if "confirmed_inputs" in row.keys() else None
        if row["state"] == "finished":
            result = {**json.loads(row["result"]), "operation_id": op}
            if snapshot is None and result.get("status") == "saved":
                result.update(status="saved_needs_review", message="历史操作缺少确认内容，请人工核对。")
            return result
        if row["state"] == "prepared":
            return {"operation_id": op, "status": "awaiting_confirmation"}
        receipt_id = row["receipt_id"]
        if isinstance(receipt_id, str):
            receipt_id = json.loads(receipt_id.removeprefix("json:"))
        if receipt_id is None:
            receipt_id = self._recover_receipt(op)
        if receipt_id is None:
            return {"operation_id": op, "status": "needs_reconciliation",
                    "message": "提交结果待核对，请在原系统核对记录。请勿重新提交。"}
        result = json.loads(row["result"] or "{}")
        result.update(operation_id=op, id=receipt_id)
        confirmed = json.loads(snapshot) if snapshot is not None else None
        if not confirmed or _digest([plan, confirmed]) != row["binding"]:
            return {**result, "status": "saved_needs_review",
                    "message": "缺少可用于当前业务定义的确认内容，请在原系统核对。"}
        try:
            if inline_detail is not None:
                try:
                    fields = readback_fields(readback_view, plan, inline_detail, receipt_id)
                except (KeyError, TypeError, ValueError):
                    inline_detail = None
            detail = inline_detail
            if detail is None:
                detail = await read_detail(row["route"], receipt_id)
                fields = readback_fields(readback_view, plan, detail, receipt_id)
        except Exception:
            return {**result, "status": "saved_needs_readback",
                    "message": "已取得保存回执，请按操作 ID 重试回查。"}
        differences = differing_fields(confirmed, fields)
        if differences:
            return {**result, "status": "saved_needs_review", "differing_fields": differences,
                    "message": "记录已保存，回查内容与确认内容有差异，请核对列出的字段。"}
        result.update(status="saved", detail=detail)
        self.finish(op, result)
        return result
