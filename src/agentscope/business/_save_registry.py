"""Register draft creation tools that reuse the original client's save logic."""

import asyncio
import hashlib
import json
import re
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, closing
from pathlib import Path

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool
from agentscope.tool._business_search import PublishedBusinessTool

from ._business import bind_operation, write_definition
from ._save_operation import SaveOperations


def _readback_business_fields(saved):
    """Verify parent links before comparing independently generated identities."""
    document = saved["detail"]
    order_id = saved["id"]
    if not isinstance(document, dict):
        raise ValueError("回查单据格式无效")
    header = document.get("order", document)
    if not isinstance(header, dict) or order_id is None or header.get("id") != order_id:
        raise ValueError("回查单据 ID 与保存结果不符")
    detail_positions = {}
    for position, row in enumerate(document.get("details", [])):
        if not isinstance(row, dict):
            raise ValueError("回查明细格式无效")
        if "id" in row:
            detail_id = row["id"]
            if detail_id is None or detail_id in detail_positions:
                raise ValueError("回查明细 ID 为空或重复")
            detail_positions[detail_id] = position

    flat = object()

    def normalize(value, parent_id=flat, *, detail_row=False):
        if isinstance(value, list):
            return [normalize(row, parent_id) for row in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == "orderId":
                if item != order_id:
                    raise ValueError("回查行属于其他单据")
                result[key] = "<order>"
            elif key == "detailId":
                # Nested rows must reference their enclosing row. A flat list
                # may reference a sibling detail by its generated identifier.
                if parent_id is not flat:
                    if parent_id is None or item != parent_id:
                        raise ValueError("回查子行不属于所在明细")
                    result[key] = "<parent>"
                elif item in detail_positions:
                    result[key] = detail_positions[item]
                else:
                    raise ValueError("回查子行没有对应明细")
            elif value is document and key == "details":
                result[key] = [normalize(row, detail_row=True) for row in item]
            elif key not in {
                "id", "billNo", "createdTime", "updatedTime", "detailBillNo", "batchBillNo",
            }:
                result[key] = normalize(item, value.get("id") if detail_row else parent_id)
        return result

    return normalize(document)


def _same_saved_draft(original, generated):
    if not generated.get("detail") or original["id"] == generated.get("id"):
        return False
    try:
        return (
            original["request"] == generated["request"]
            and _readback_business_fields(original) == _readback_business_fields(generated)
        )
    except (KeyError, TypeError, ValueError):
        return False


def _actions_for(plan, inputs):
    actions = []
    for step in plan["actions"]:
        action = dict(step)
        if (condition := action.pop("when", None)) and inputs.get(
            condition,
        ) in (None, ""):
            continue
        if field := action.pop("field", None):
            value = inputs.get(field)
            if value is None:
                continue
            action["text" if action["action"] == "click" else "value"] = str(value)
        actions.append(action)
    return actions


@asynccontextmanager
async def _publication_lock(path: Path):
    """Serialize publication and use across workers sharing this directory.

    SQLite releases the write lock if the process exits. The separate pending
    checkpoint survives a crash after the first ERP write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path.with_suffix(".lock"), timeout=0)) as lock:
        while True:
            try:
                lock.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                    raise
                await asyncio.sleep(0.05)
        yield


_ACCEPTANCE_VERSION = 3


class SaveBusinessRegistry:
    """Compile a scoped client action plan and require both save outcomes."""

    def __init__(
        self,
        source: Path,
        openapi: Path,
        storage: Path,
        capture: Callable[[dict, list[dict], bool], Awaitable[dict]],
        read_detail: Callable[[str, int], Awaitable[dict]],
        *,
        target_label: str | None = None,
    ):
        self.source = source.resolve()
        self.openapi = openapi
        self.storage = storage
        self.capture = capture
        self.read_detail = read_detail
        self.target_label = (target_label or "").strip()

    def tools(self, user_id, agent_id):
        key = json.dumps([user_id, agent_id]).encode()
        directory = self.storage / hashlib.sha256(key).hexdigest()

        async def register_save_business_tool(
            business: str,
            client_source: str,
            client_route: str,
            save_route: str,
            detail_route: str,
            issue_selector: str,
            actions: list[dict],
            missing: dict,
            complete: dict,
            counterexamples: list[dict] | None = None,
        ) -> dict:
            """生成并验收 ERP 新增草稿及回查。验收会在绑定账套新增两张草稿。

            Args:
                business: 用户提供的业务名称。
                client_source: 从源码查到的实际 Vue 页面相对路径。
                client_route: 原客户端页面路由。
                save_route: OpenAPI 新增 POST 路径。
                detail_route: OpenAPI 回查 GET 路径，保留路径参数。
                issue_selector: 源码中保存拦截提示的 CSS 选择器。
                actions: 从打开新增到点击保存的完整步骤。每个步骤使用
                    action 为 click、fill、press 或 wait，selector 是 CSS，
                    也可用 button 或 text 定位可见文字。value 是写入的文本或键名。
                    文本录入步骤可用 field 代替 value 以绑定业务输入；
                    按文字点击也可用 field 代替 text。wait 的 state 可用 hidden。
                    when 可绑定字段，字段缺填时跳过该页面步骤。
                missing: 缺填样本，含 inputs 对象和原页面具体 actions。
                complete: 补齐样本，使用同一份业务数据补全必填项，结构同 missing。
                counterexamples: Agent 从源码和 API 自行提出的 1 至 5 个拒绝样本。
                    各含 name、rule、evidence、inputs 和 actions。evidence 包含
                    源码相对 path 与行号 line。以补齐样本为基线，改变须被拒绝的输入。
                    至少一组须独立于普通缺填样本，覆盖不同的实际输入和操作。
                    原客户端或生成工具尝试写入都会阻止注册，反例不发送到 ERP。
            """
            return await self.register(
                directory, business, client_source, client_route,
                save_route, detail_route, issue_selector, actions, missing, complete,
                counterexamples,
            )

        return [
            FunctionTool(
                register_save_business_tool,
                is_concurrency_safe=False,
                permission=PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    bypass_immune=True,
                    message=(
                        f"在账套「{self.target_label}」中执行保存验收？"
                        "原客户端和生成工具将各创建一张草稿，共两张。"
                        if self.target_label else "当前尚未配置目标账套，仅可预览。"
                    ),
                ),
            ),
            *(
                tool
                for path in sorted(directory.glob("*.json"))
                for tool in self._published_tools(path)
            ),
        ]

    def _current(self, record):
        report = record["verification"]
        plan = record["plan"]
        if report.get("version") != _ACCEPTANCE_VERSION or not report.get("registered"):
            return False
        return (
            bool(self.target_label)
            and plan.get("targetLabel") == self.target_label
            and self._sources_current(plan)
        )

    def _sources_current(self, plan):
        try:
            return (
                plan["apiSha256"] == hashlib.sha256(self.openapi.read_bytes()).hexdigest()
                and all(self._source_hash(name) == digest for name, digest in plan["ruleHashes"].items())
            )
        except (OSError, ValueError):
            return False

    def _source_hash(self, name):
        source = (self.source / name).resolve()
        if not source.is_relative_to(self.source):
            raise ValueError("规则来源超出绑定源码目录")
        return hashlib.sha256(source.read_bytes()).hexdigest()

    def _published_tools(self, path):
        record = json.loads(path.read_bytes())
        save, read = self._runtime(record["plan"], path)
        return [save, read] if self._current(record) else [read]

    def _runtime(self, plan, path):
        operations = SaveOperations(path.parent)

        def bind_confirmation(session_id, reply_id, call_id, inputs):
            op = operations.prepare(plan, inputs, session_id, reply_id, call_id)
            return op, f"在账套「{plan['targetLabel']}」新增一张{plan['business']}草稿？操作 ID：{op}"

        async def save(*, _confirmation_id=None, _confirmed_inputs=None, **inputs):
            async with _publication_lock(path):
                record = json.loads(path.read_bytes())
                if record["plan"] != plan or not self._current(record):
                    raise ValueError("保存工具已停用，请重新验收。")
                op = _confirmation_id
                if not op:
                    # Pre-upgrade approvals have no durable evidence that
                    # another worker did or did not already submit the POST.
                    return {"status": "needs_reconciliation",
                            "message": "缺少原保存操作 ID，请在原账套核对是否已经建单。"}
                draft = _confirmed_inputs if _confirmed_inputs is not None else inputs
                if operations.claim(op, plan, draft):
                    actions = _actions_for(plan, draft)
                    actions[-1]["saveReceipt"] = operations.receipt_slot(op)
                    try:
                        saved = await self.capture(plan, actions, True)
                    except Exception:
                        # The adapter may have sent the POST before failing.
                        saved = {}
                    receipt_id = saved.get("id")
                    if type(receipt_id) is int and receipt_id > 0:
                        operations.record_receipt(op, receipt_id, saved)
                        detail = saved.get("detail")
                        header = detail.get("order", detail) if isinstance(detail, dict) else None
                        if isinstance(header, dict) and header.get("id") == receipt_id:
                            operations.finish(op, {**saved, "status": "saved"})
                    elif saved.get("attempted") is False:
                        operations.finish(op, {**saved, "status": "blocked"})
            return await operations.recover(op, plan, self.read_detail)

        async def read(receipt_id: int | None = None, operation_id: str | None = None):
            """按保存操作 ID 恢复，或根据 ERP 内部 ID 回查单据。"""
            if (operation_id is None) == (receipt_id is None):
                raise ValueError("请提供操作 ID 或单据 ID 中的一项。")
            if operation_id is not None:
                if plan.get("targetLabel") != self.target_label:
                    raise ValueError("账套已变更，请在原账套核对。")
                return await operations.recover(operation_id, plan, self.read_detail)
            return await self.read_detail(plan["detailRoute"], receipt_id)

        # Keep legacy read-by-ID calls working. Only discover records bound
        # to the current ledger in a new text conversation.
        same_ledger = self.target_label and plan.get("targetLabel") == self.target_label
        business = plan["business"] if same_ledger else None
        save_tool = PublishedBusinessTool(
            save,
            business=business, action="save",
            name=plan["saveTool"],
            description=f"新增{plan['business']}草稿并回查。保留原客户端的字段校验。"
            "每次调用都会申请新增一张，仅在用户要求新增时使用。恢复或重试请用回查工具和原操作 ID。",
            input_schema={
                "type": "object",
                "properties": {
                    field: {"type": ["string", "number"]}
                    for field in plan["fields"]
                },
                "additionalProperties": False,
            },
            is_concurrency_safe=False,
            permission=PermissionDecision(
                behavior=PermissionBehavior.ASK, bypass_immune=True,
                message=f"在账套「{self.target_label}」新增草稿？",
            ),
        )
        save_tool.bind_confirmation = bind_confirmation
        return [
            save_tool,
            PublishedBusinessTool(
                read,
                business=business, action="readback",
                name=plan["readTool"],
                description=f"按 operation_id 恢复{plan['business']}保存结果，或按 receipt_id 回查单据。只读。",
                is_read_only=True,
                permission=PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message="Read a saved draft on the bound ERP.",
                ),
            ),
        ]

    def _validate_counterexamples(self, cases, fields, complete, missing, actions):
        if not isinstance(cases, list) or not 1 <= len(cases) <= 5:
            raise ValueError("注册前请从源码和 API 提交 1 至 5 个应被拒绝的反例。")
        independent = False
        missing_inputs = missing["inputs"]
        repaired_fields = {
            key for key, value in complete["inputs"].items()
            if missing_inputs.get(key) != value
        }
        baseline = _actions_for({"actions": actions}, missing_inputs)
        names = set()
        variants = set()
        for case in cases:
            name, rule = case.get("name"), case.get("rule")
            if not isinstance(name, str) or not name.strip() or name in names:
                raise ValueError("反例须有唯一名称。")
            if not isinstance(rule, str) or not rule.strip():
                raise ValueError("请说明反例违反的业务规则。")
            evidence = case.get("evidence", {})
            path, line = evidence.get("path"), evidence.get("line")
            if not isinstance(path, str) or type(line) is not int or line < 1:
                raise ValueError("反例须附源码路径与行号。")
            source = (self.source / path).resolve()
            if (
                not source.is_relative_to(self.source)
                or source.suffix not in {".vue", ".js", ".ts", ".mjs", ".java", ".json"}
                or not source.is_file()
                or line > len(source.read_text().splitlines())
            ):
                raise ValueError("反例的源码依据无法复核。")
            inputs = case.get("inputs")
            if (
                not isinstance(inputs, dict)
                or set(inputs) - set(fields)
                or inputs == complete["inputs"]
            ):
                raise ValueError("反例必须改变补齐样本的已绑定业务输入。")
            steps = case.get("actions")
            if not isinstance(steps, list) or not steps or steps[-1].get("action") != "click":
                raise ValueError("反例必须在原客户端实际点击保存。")
            variant = json.dumps(inputs, sort_keys=True)
            if variant in variants:
                raise ValueError("反例须使用不同的输入。")
            if (
                steps != missing["actions"]
                and _actions_for({"actions": actions}, inputs) != baseline
                and any(
                    inputs.get(key) not in (missing_inputs.get(key), None, "")
                    for key in repaired_fields
                )
            ):
                independent = True
            names.add(name)
            variants.add(variant)
        if not independent:
            raise ValueError("至少一个反例须区别于普通缺填用例并实际改变操作。")

    async def _check_counterexamples(self, plan, cases):
        observations = []
        for case in cases:
            original = await self.capture(plan, case["actions"], False)
            generated = await self.capture(
                plan, _actions_for(plan, case["inputs"]), False,
            )
            blocked = (
                original.get("attempted") is False
                and generated.get("attempted") is False
                and bool(original.get("issue"))
                and original["issue"] == generated.get("issue")
            )
            observations.append({
                "name": case["name"],
                "rule": case["rule"],
                "evidence": case["evidence"],
                "passed": blocked,
                "originalAttemptedSave": original.get("attempted"),
                "generatedAttemptedSave": generated.get("attempted"),
                "originalIssue": original.get("issue", ""),
                "generatedIssue": generated.get("issue", ""),
                "erpWrites": 0,
            })
        return observations

    async def register(
        self, directory, business, source_file, client_route, save_route,
        detail_route, issue_selector, actions, missing, complete, counterexamples=None,
    ):
        if not business.strip() or not re.fullmatch(r"/[\w/-]+", client_route):
            raise ValueError("需要业务名称及原客户端相对路由")
        if not re.fullmatch(r"/[\w/-]+", save_route) or not re.fullmatch(
            re.escape(save_route) + r"/\{\w+\}", detail_route
        ):
            raise ValueError("仅支持同一业务的新增和按 ID 回查")
        source = (self.source / source_file).resolve()
        if not source.is_relative_to(self.source) or source.suffix != ".vue":
            raise ValueError("请选择绑定源码中的 Vue 页面")
        raw = source.read_bytes()
        text = raw.decode()
        issue_class = re.match(r"\.([\w-]+)", issue_selector)
        if save_route not in text or not issue_class or issue_class[1] not in text:
            raise ValueError("页面源码中找不到保存路径或拦截提示")
        document = json.loads(self.openapi.read_bytes())
        save = bind_operation(document, {"method": "POST", "template": save_route})
        detail = bind_operation(document, {
            "method": "GET",
            "template": re.sub(r"\{\w+\}", "{0}", detail_route),
        })
        if len(detail["pathNames"]) != 1 or save["bodySchema"] is None:
            raise ValueError("OpenAPI 缺少保存请求体或内部 ID 回查")
        fields = sorted({step["field"] for step in actions if step.get("field")})
        if not fields or not all(field.isidentifier() for field in fields):
            raise ValueError("需要将页面操作绑定到命名的业务字段")
        if any(step.get("when") and step["when"] not in fields for step in actions):
            raise ValueError("页面步骤的执行条件须绑定到业务字段。")
        for case in (missing, complete):
            if set(case["inputs"]) - set(fields):
                raise ValueError("验收数据包含未绑定的业务字段")
        for recipe in (actions, missing["actions"], complete["actions"]):
            if not recipe or recipe[-1].get("action") != "click":
                raise ValueError("每组页面步骤最后一步必须点击保存")
            if any("saveReceipt" in step for step in recipe):
                raise ValueError("saveReceipt 是宿主分配的回执，不能出现在验收步骤中。")
        if missing["inputs"] == complete["inputs"] or any(
            complete["inputs"].get(key) != value
            for key, value in missing["inputs"].items()
            if value not in (None, "")
        ):
            raise ValueError("补齐组应保留缺填组已有的业务数据")
        plan = {
            "business": business,
            "targetLabel": self.target_label,
            "clientRoute": client_route,
            "saveRoute": save_route,
            "detailRoute": detail_route,
            "issueSelector": issue_selector,
            "actions": actions,
            "fields": fields,
            "saveTool": "erp_save_" + save["name"],
            "readTool": "erp_read_" + detail["name"],
            "source": {"file": source_file, "sha256": hashlib.sha256(raw).hexdigest()},
            "apiSha256": hashlib.sha256(self.openapi.read_bytes()).hexdigest(),
        }
        if max(len(plan["saveTool"]), len(plan["readTool"])) > 64:
            raise ValueError("OpenAPI 操作 ID 超出工具名限制")
        path = directory / f"{plan['saveTool']}.json"
        checkpoint = directory / f".{plan['saveTool']}.pending"
        acceptance = hashlib.sha256(json.dumps(
            [missing, complete, counterexamples], ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest()
        async with _publication_lock(path):
            current = json.loads(path.read_bytes()) if path.exists() else None
            try:
                self._validate_counterexamples(counterexamples, fields, complete, missing, actions)
                plan["ruleHashes"] = {
                    name: self._source_hash(name)
                    for name in {source_file, *(case["evidence"]["path"] for case in counterexamples)}
                }
            except (ValueError, OSError):
                if current:
                    current["verification"]["registered"] = False
                    write_definition(path, current)
                raise
            if checkpoint.exists():
                return {
                    "registered": False,
                    "stage": "verify_prior_save",
                    "message": "此前已开始写入验收。请回查已知单号，避免重复保存。",
                    "checkpoint": json.loads(checkpoint.read_text()),
                }
            if current:
                if self._current(current) and current["plan"] == plan and current["verification"].get(
                    "acceptanceSha256",
                ) == acceptance:
                    return current["verification"]
                # Persist suspension before the first capture, including exceptions or cancellation.
                current["verification"]["registered"] = False
                write_definition(path, current)

            observations = await self._check_counterexamples(plan, counterexamples)
            if not all(case["passed"] for case in observations):
                return {
                    "registered": False,
                    "stage": "counterexamples",
                    "message": "反例未在保存前被两端同样拦截，已阻止注册。",
                    "counterexamples": observations,
                    "erpWrites": 0,
                }
            original = await self.capture(plan, missing["actions"], False)
            generated = await self.capture(plan, _actions_for(plan, missing["inputs"]), False)
            if original.get("attempted") or generated.get("attempted") or (
                not original.get("issue") or original["issue"] != generated.get("issue")
            ):
                raise ValueError("缺填组未得到相同的保存前拦截，未发出 ERP 写入")

            original_preview = await self.capture(plan, complete["actions"], False)
            generated_preview = await self.capture(plan, _actions_for(plan, complete["inputs"]), False)
            if not original_preview.get("attempted") or not generated_preview.get(
                "attempted"
            ) or original_preview["request"] != generated_preview["request"]:
                return {
                    "registered": False,
                    "stage": "preview",
                    "originalIssue": original_preview.get("issue"),
                    "generatedIssue": generated_preview.get("issue"),
                    "erpWrites": 0,
                }
            if not self._sources_current(plan):
                raise ValueError("验收期间规则已变化，请重新验收。")
            if not self.target_label:
                return {
                    "registered": False, "stage": "target_required", "erpWrites": 0,
                    "message": "预览已完成。请由宿主配置 target_label 标明目标账套后再申请写入验收。",
                }
            directory.mkdir(parents=True, exist_ok=True)
            write_definition(checkpoint, {"stage": "original_save"})
            original_saved = await self.capture(plan, complete["actions"], True)
            if not original_saved.get("detail"):
                if not original_saved.get("attempted"):
                    checkpoint.unlink()
                else:
                    write_definition(checkpoint, {
                        "stage": "original_readback", "originalId": original_saved.get("id"),
                    })
                return {"registered": False, "stage": "original_save", "result": original_saved}
            write_definition(checkpoint, {
                "stage": "generated_save", "originalId": original_saved["id"],
            })
            generated_saved = await self.capture(plan, _actions_for(plan, complete["inputs"]), True)
            write_definition(checkpoint, {
                "stage": "compare_saved", "originalId": original_saved["id"],
                "generatedId": generated_saved.get("id"),
            })
            if not _same_saved_draft(original_saved, generated_saved):
                return {
                    "registered": False,
                    "stage": "compare_saved",
                    "originalId": original_saved.get("id"),
                    "generatedId": generated_saved.get("id"),
                    "requestEqual": original_saved.get("request") == generated_saved.get("request"),
                    "generatedIssue": generated_saved.get("issue"),
                }
            report = {
                "registered": True,
                "version": _ACCEPTANCE_VERSION,
                "business": business,
                "acceptanceSha256": acceptance,
                "counterexamples": observations,
                "missing": {"issue": original["issue"], "erpWrites": 0},
                "complete": {
                    "originalId": original_saved["id"],
                    "generatedId": generated_saved["id"],
                    "requestEqual": True,
                    "readbackBusinessFieldsEqual": True,
                },
                "tools": [plan["saveTool"], plan["readTool"]],
                "available": "原会话下一条消息",
            }
            if not self._sources_current(plan):
                raise ValueError("保存回查期间规则已变化，请核对写入检查点。")
            write_definition(path, {"plan": plan, "verification": report})
            checkpoint.unlink()
        return report
