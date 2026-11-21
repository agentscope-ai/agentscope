"""Register draft creation tools that reuse the original client's save logic."""

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool

from ._business import bind_operation, write_definition


def _without_generated_identity(value):
    if isinstance(value, list):
        return [_without_generated_identity(row) for row in value]
    if isinstance(value, dict):
        return {
            key: _without_generated_identity(item)
            for key, item in value.items()
            if key not in {
                "id", "orderId", "detailId", "billNo", "createdTime", "updatedTime",
                "detailBillNo", "batchBillNo",
            }
        }
    return value


def _actions_for(plan, inputs):
    actions = []
    for step in plan["actions"]:
        action = dict(step)
        if field := action.pop("field", None):
            value = inputs.get(field)
            if value is None:
                continue
            action["text" if action["action"] == "click" else "value"] = str(value)
        actions.append(action)
    return actions


class SaveBusinessRegistry:
    """Compile a scoped client action plan and require both save outcomes."""

    def __init__(
        self,
        source: Path,
        openapi: Path,
        storage: Path,
        capture: Callable[[dict, list[dict], bool], Awaitable[dict]],
        read_detail: Callable[[str, int], Awaitable[dict]],
    ):
        self.source = source.resolve()
        self.openapi = openapi
        self.storage = storage
        self.capture = capture
        self.read_detail = read_detail
        self.lock = asyncio.Lock()

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
        ) -> dict:
            """生成并验收 ERP 新增草稿及回查，成功后注册到原会话。

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
                missing: 缺填样本，含 inputs 对象和原页面具体 actions。
                complete: 补齐样本，使用同一份业务数据补全必填项，结构同 missing。
            """
            return await self.register(
                directory, business, client_source, client_route,
                save_route, detail_route, issue_selector, actions, missing, complete,
            )

        return [
            FunctionTool(
                register_save_business_tool,
                is_concurrency_safe=False,
                permission=PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message="Run the requested draft acceptance on the bound test ERP.",
                ),
            ),
            *(
                tool
                for path in sorted(directory.glob("*.json"))
                for tool in self._runtime(json.loads(path.read_text())["plan"])
            ),
        ]

    def _runtime(self, plan):
        async def save(**inputs):
            saved = await self.capture(plan, _actions_for(plan, inputs), True)
            # Preserve a successful POST's identifier even if readback failed.
            if saved.get("id") and not saved.get("detail"):
                return {**saved, "status": "saved_needs_readback"}
            return saved

        async def read(receipt_id: int):
            """根据保存返回的内部 ID 从 ERP 回查已保存草稿。"""
            return await self.read_detail(plan["detailRoute"], receipt_id)

        return [
            FunctionTool(
                save,
                name=plan["saveTool"],
                description=f"新增{plan['business']}草稿并回查。保留原客户端的字段校验。"
                "只有用户要求保存时才调用；保存成功后只重试回查。",
                input_schema={
                    "type": "object",
                    "properties": {
                        field: {"type": ["string", "number"]}
                        for field in plan["fields"]
                    },
                    "additionalProperties": False,
                },
                is_concurrency_safe=False,
            ),
            FunctionTool(
                read,
                name=plan["readTool"],
                description=f"按{plan['business']}保存返回的内部 ID 回查。",
                is_read_only=True,
                permission=PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message="Read a saved draft on the bound ERP.",
                ),
            ),
        ]

    async def register(
        self, directory, business, source_file, client_route, save_route,
        detail_route, issue_selector, actions, missing, complete,
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
        for case in (missing, complete):
            if set(case["inputs"]) - set(fields):
                raise ValueError("验收数据包含未绑定的业务字段")
        for recipe in (actions, missing["actions"], complete["actions"]):
            if not recipe or recipe[-1].get("action") != "click":
                raise ValueError("每组页面步骤最后一步必须点击保存")
        if missing["inputs"] == complete["inputs"] or any(
            complete["inputs"].get(key) != value
            for key, value in missing["inputs"].items()
            if value not in (None, "")
        ):
            raise ValueError("补齐组应保留缺填组已有的业务数据")
        plan = {
            "business": business,
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
        async with self.lock:
            if path.exists():
                return json.loads(path.read_text())["verification"]
            if checkpoint.exists():
                return {
                    "registered": False,
                    "stage": "verify_prior_save",
                    "message": "此前已开始写入验收。请回查已知单号，避免重复保存。",
                    "checkpoint": json.loads(checkpoint.read_text()),
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
            keys = ("request", "detail")
            if not generated_saved.get("detail") or any(
                (
                    original_saved[key] != generated_saved[key] if key == "request"
                    else _without_generated_identity(original_saved[key])
                    != _without_generated_identity(generated_saved[key])
                )
                for key in keys
            ) or original_saved["id"] == generated_saved["id"]:
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
                "business": business,
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
            write_definition(path, {"plan": plan, "verification": report})
            checkpoint.unlink()
        return report
