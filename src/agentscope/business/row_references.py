# -*- coding: utf-8 -*-
"""Source-backed list selections for read-only business tools."""
import asyncio
import hashlib
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Awaitable, Callable
from contextlib import closing
from dataclasses import astuple, dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool
from ._business import bind_operation, node


@dataclass(frozen=True)
class RowScope:
    """Trusted host identity for a connector invocation."""

    system: str
    tenant: str
    user: str
    session: str

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v.strip() for v in astuple(self)):
            raise ValueError("行引用需要系统、账套、用户和会话")


@dataclass(frozen=True)
class RowActionSource:
    """Trusted entry points. The row keys are inferred, never configured."""

    root: Path
    openapi: Path
    page: str
    handler: str
    list_route: str
    detail_route: str

    def discover(self) -> dict:
        """Trace the clicked row and check the destination GET contract."""
        raw = self.openapi.read_bytes()
        contract = json.loads(raw)
        template = re.sub(r"\{[^{}]+\}", "{0}", self.detail_route)
        detail = bind_operation(
            contract,
            {"method": "GET", "template": template},
        )
        if (
            "get" not in contract["paths"].get(self.list_route, {})
            or detail["route"] != self.detail_route
            or not detail["pathNames"]
            or detail["bodySchema"] is not None
            or any(
                p.get("required") and p["in"] != "path"
                for p in detail["parameters"]
            )
        ):
            raise ValueError("缺少仅依赖行路径参数的只读详情契约或列表 GET")
        plan = node(
            "extract.mjs",
            str(self.root.resolve()),
            "--row-action",
            data={
                "page": self.page,
                "handler": self.handler,
                "listRoute": self.list_route,
                "detailRoute": self.detail_route,
            },
        )
        if set(plan["fields"]) != set(detail["pathNames"]):
            raise ValueError("源码字段没有覆盖详情路径参数")
        plan["schemas"] = {
            p["name"]: p["schema"]
            for p in detail["parameters"]
            if p["in"] == "path"
        }
        plan["contractHash"] = hashlib.sha256(raw).hexdigest()
        plan["revision"] = hashlib.sha256(
            json.dumps(
                [self.list_route, self.detail_route, self.handler, plan],
                sort_keys=True,
            ).encode(),
        ).hexdigest()
        return plan

    def current(self, plan: dict) -> bool:
        """Check exact bytes, including symlink confinement on re-read."""
        root = self.root.resolve()
        for name, expected in plan["hashes"].items():
            file = (root / name).resolve()
            if not file.is_relative_to(root) or not file.is_file():
                return False
            if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                return False
        return (
            hashlib.sha256(self.openapi.read_bytes()).hexdigest()
            == plan["contractHash"]
        )


def _arguments(row: dict, plan: dict) -> dict:
    values = {}
    for param, origin in plan["fields"].items():
        value: object = row
        label = ".".join(origin["path"])
        for key in origin["path"]:
            value = value.get(key) if isinstance(value, dict) else None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, str))
            or value == ""
        ):
            raise ValueError(f"列表行缺少可用字段 {label}")
        if origin["cast"] == "Number":
            # A rounded JavaScript key would address a different record.
            if (
                not re.fullmatch(r"-?[0-9]{1,32}", str(value))
                or abs(int(value)) > 2**53 - 1
            ):
                raise ValueError(f"字段 {label} 不能无损转换成客户端数字键")
            value = int(value)
        elif origin["cast"] == "String":
            value = str(value)
        if not Draft202012Validator(plan["schemas"][param]).is_valid(value):
            raise ValueError(f"字段 {label} 与详情参数类型不符")
        values[param] = value
    return values


class OpenRowTool(FunctionTool):
    """The authorized row reader that a message card may dispatch."""


class RowDetailTools:
    """Bind a list/detail pair to authorized reads and durable selections.

    Both readers must use the supplied scope and the current upstream
    credentials, including object-level authorization. Return only list/detail
    fields the principal may see. The list reader supplies the client's row
    objects after any client normalization and before display projection.
    """

    def __init__(
        self,
        source: RowActionSource,
        database: Path,
        *,
        name: str,
        visible_fields: tuple[str, ...],
        scope: Callable[[], RowScope],
        can_read: Callable[[RowScope, str], bool],
        list_rows: Callable[[RowScope, int], Awaitable[dict]],
        read_detail: Callable[[RowScope, dict], Awaitable[dict]],
        title: str | None = None,
        field_labels: dict[str, str] | None = None,
    ) -> None:
        self.source = source
        self.database = database
        self.name = name
        self.visible_fields = visible_fields
        self.card = {
            "version": 1,
            "title": title or name,
            "columns": [
                {"key": key, "label": (field_labels or {}).get(key, key)}
                for key in visible_fields
            ],
        }
        self.scope = scope
        self.can_read = can_read
        self.list_rows = list_rows
        self.read_detail = read_detail
        self._plan: dict | None = None
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS row_lists "
                "(ref TEXT PRIMARY KEY, owner TEXT, revision TEXT,"
                " expires INTEGER, args TEXT)",
            )

    def _authorize(
        self,
        action: str,
        expected: RowScope | None = None,
    ) -> RowScope:
        current = self.scope()
        if (
            not isinstance(current, RowScope)
            or (expected and current != expected)
            or not self.can_read(current, action)
        ):
            raise PermissionError("当前身份或账套无权执行此读取")
        return current

    def _current_plan(self) -> dict:
        plan = self._plan
        if not plan or not self.source.current(plan):
            plan = self.source.discover()
            self._plan = plan
        return plan

    async def inspect_row_action(self) -> dict:
        """查看列表行到详情各参数的源码字段、源文件、行号和版本。"""
        scope = self._authorize("source")
        plan = await asyncio.to_thread(self._current_plan)
        self._authorize("source", scope)
        return {
            "list_route": self.source.list_route,
            "detail_route": self.source.detail_route,
            "fields": plan["fields"],
            "evidence": plan["evidence"],
            "revision": plan["revision"],
        }

    def _owner(self, scope: RowScope) -> str:
        return json.dumps(
            [
                *astuple(scope),
                self.name,
                self.source.list_route,
                self.source.detail_route,
            ],
        )

    async def list_recent(self, limit: int = 5) -> dict:
        """按返回顺序逐条编号展示，包括内容相同的行。追问时沿用本次 list_ref。"""
        scope = self._authorize(self.source.list_route)
        if type(limit) is not int or limit < 1:
            raise ValueError("查询条数必须为正整数")
        plan = await asyncio.to_thread(self._current_plan)
        self._authorize(self.source.list_route, scope)
        page = await self.list_rows(scope, limit)
        self._authorize(self.source.list_route, scope)
        return self._snapshot(scope, page, plan)

    async def present_page(self, scope: RowScope, page: dict) -> dict:
        """Snapshot the exact page already fetched by a scoped list reader."""
        self._authorize(self.source.list_route, scope)
        plan = await asyncio.to_thread(self._current_plan)
        self._authorize(self.source.list_route, scope)
        return self._snapshot(scope, page, plan)

    def _snapshot(self, scope: RowScope, page: dict, plan: dict) -> dict:
        args: list[dict] = []
        records = []
        for row in page["records"]:
            try:
                args.append({"params": _arguments(row, plan)})
            except ValueError as exc:
                args.append({"missing": str(exc)})
            records.append({key: row.get(key) for key in self.visible_fields})
        ref = secrets.token_urlsafe(24)
        now = int(time.time())
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("DELETE FROM row_lists WHERE expires <= ?", (now,))
            conn.execute(
                "INSERT INTO row_lists VALUES (?, ?, ?, ?, ?)",
                (
                    ref,
                    self._owner(scope),
                    plan["revision"],
                    now + 86400,
                    json.dumps(args),
                ),
            )
            conn.commit()
        return {
            "list_ref": ref,
            "records": records,
            "total": page["total"],
            "row_card": self.card,
        }

    async def open_row(self, list_ref: str, row_number: int) -> dict:
        """打开原列表中从 1 开始的第几行。使用原 list_ref，直接查详情。"""
        scope = self._authorize(self.source.detail_route)
        self._authorize(self.source.list_route, scope)
        if type(row_number) is not int or row_number <= 0:
            raise ValueError("行号从 1 开始")
        with closing(sqlite3.connect(self.database)) as conn:
            saved = conn.execute(
                "SELECT revision, args FROM row_lists "
                "WHERE ref=? AND owner=? AND expires>?",
                (list_ref, self._owner(scope), int(time.time())),
            ).fetchone()
        if saved is None:
            raise ValueError("该列表引用已失效或不属于当前会话和账套")
        plan = await asyncio.to_thread(self._current_plan)
        if saved[0] != plan["revision"]:
            raise ValueError("列表到详情的源码或契约已变化，请重新查询列表")
        rows = json.loads(saved[1])
        if row_number > len(rows):
            raise ValueError("行号超出原列表")
        selection = rows[row_number - 1]
        if "missing" in selection:
            raise ValueError(selection["missing"])
        self._authorize(self.source.list_route, scope)
        self._authorize(self.source.detail_route, scope)
        result = await self.read_detail(scope, selection["params"])
        self._authorize(self.source.list_route, scope)
        self._authorize(self.source.detail_route, scope)
        return result

    def tools(self) -> list[FunctionTool]:
        """Install this connector in an Agent's ordinary Toolkit."""
        allow = PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="只读连接，按当前身份验证。",
        )
        tools = [
            FunctionTool(fn, name=name, permission=allow, is_read_only=True)
            for fn, name in (
                (self.inspect_row_action, f"inspect_{self.name}_row_action"),
                (self.list_recent, f"query_recent_{self.name}"),
            )
        ]
        tools.append(
            OpenRowTool(
                self.open_row,
                name=f"open_{self.name}_row",
                permission=allow,
                is_read_only=True,
            ),
        )
        return tools
