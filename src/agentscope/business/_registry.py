"""Publish source-backed tools after a live client comparison."""

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool

from ._business import as_tool, build, preview, write_definition

_ALLOW = PermissionDecision(
    behavior=PermissionBehavior.ALLOW,
    message="Verified read-only business operation on the bound ERP.",
)


def _input_from_capture(artifact: dict, query: dict) -> dict:
    plan = artifact["plan"]
    request = plan["functions"][plan["entry"]]["steps"][-1]["return"]
    argument = request.get("query", {}).get("variable")
    if argument is None:
        if not artifact["inputSchema"]["required"]:
            return {}
        raise ValueError("Provide inputs for an adapter with transformed arguments")
    converters = {
        "integer": int, "number": float, "boolean": {"true": True, "false": False}.__getitem__,
        "string": str,
    }
    parameters = {
        p["name"]: p["schema"]["type"]
        for p in artifact["operation"]["parameters"]
        if p["in"] == "query"
    }
    return {
        argument: {
            name: converters[parameters[name]](value)
            for name, value in query.items()
        },
    }


def _route_regex(route: str) -> str:
    """Match a concrete browser request against the OpenAPI route."""
    return "/".join(
        r"[^/]+" if re.fullmatch(r"\{\w+\}", part) else re.escape(part)
        for part in route.split("/")
    )


class BusinessRegistry:
    """Build, verify and register read-only actions for a bound client."""

    def __init__(
        self,
        source: Path,
        openapi: Path,
        storage: Path,
        base_url: str,
        get_token: Callable[[], Awaitable[str]],
        capture: Callable[[str, str, list[dict]], Awaitable[dict]],
        *,
        http_client: str | None = None,
        success: dict | None = None,
        openapi_java_object_maps: bool = False,
        volatile_envelope_fields: tuple[str, ...] = (),
    ):
        self.source = source
        self.openapi = openapi
        self.storage = storage
        self.base_url = base_url
        self.get_token = get_token
        self.capture = capture
        self.profile = {"httpClient": http_client}
        if success is not None:
            self.profile["success"] = success
        if openapi_java_object_maps:
            self.profile["openapiJavaObjectMaps"] = True
        self.volatile_envelope_fields = volatile_envelope_fields
        self.lock = asyncio.Lock()

    def _directory(self, user_id: str, agent_id: str) -> Path:
        scope = json.dumps([user_id, agent_id]).encode()
        return self.storage / hashlib.sha256(scope).hexdigest()

    def _business_result(self, payload):
        if not isinstance(payload, dict):
            return payload
        return {
            k: v for k, v in payload.items()
            if k not in self.volatile_envelope_fields
        }

    def tools(self, user_id: str, agent_id: str) -> list[FunctionTool]:
        """Attach the registration entry and all previously verified tools."""
        directory = self._directory(user_id, agent_id)

        async def register_business_tool(
            business: str,
            entry: str,
            client_route: str,
            samples: list[dict],
        ) -> dict:
            """从绑定的源码和 OpenAPI 生成只读工具，用原 ERP 页面验收并注册。

            Args:
                business: 用户指定的业务名称。
                entry: 自行从源码找到的 API 封装入口，格式为相对路径#导出符号。
                client_route: 自行找到的 ERP 页面路由，以 / 开头。
                samples: 至少两组不同的客户端用例。每组含 name 和 actions；
                    actions 可为空（打开页面自动加载），或用
                    {action: "fill"|"press"|"click", selector: CSS选择器,
                    value: 文本或键名} 操作页面。若工具不是直接传递查询参数，
                    该用例还需提供与浏览器输入等价的 inputs。
            """
            return await self.register(directory, business, entry, client_route, samples)

        return [
            FunctionTool(register_business_tool, permission=_ALLOW, is_concurrency_safe=False),
            *(
                self._runtime(json.loads(file.read_text())["artifact"])
                for file in sorted(directory.glob("*.json"))
            ),
        ]

    def _runtime(self, artifact: dict) -> FunctionTool:
        async def invoke(**inputs):
            return await as_tool(artifact, self.base_url, await self.get_token())(**inputs)

        operation = artifact["operation"]
        return FunctionTool(
            invoke,
            name=operation["name"],
            description=operation["description"],
            input_schema=artifact["inputSchema"],
            is_read_only=True,
            permission=_ALLOW,
        )

    async def register(
        self, directory: Path, business: str, entry: str, client_route: str,
        samples: list[dict],
    ) -> dict:
        if not business.strip() or not re.fullmatch(r"/[\w/-]+", client_route):
            raise ValueError("需要业务名称和原客户端的相对页面路由")
        if not 2 <= len(samples) <= 5:
            raise ValueError("需要 2 至 5 组客户端验收用例")
        artifact = await asyncio.to_thread(
            build, self.source, entry, self.openapi, self.profile
        )
        endpoint = artifact["plan"]["endpoint"]
        operation = artifact["operation"]
        if endpoint["method"] != "GET" or operation["bodySchema"] is not None:
            raise ValueError("通用注册目前支持只读 GET 功能")

        tool_name = "erp_" + operation["name"]
        if len(tool_name) > 64:
            raise ValueError("OpenAPI 操作 ID 超过工具名称的长度限制")
        operation["name"] = tool_name
        operation["description"] = (
            f"{business}。{operation['description']}。使用 ERP 返回的数据回答。"
        )
        route_regex = _route_regex(operation["route"])
        api_prefix = urlsplit(self.base_url).path.rstrip("/")
        evidence = []
        requests = set()
        invoke = self._runtime(artifact)
        for sample in samples:
            capture = await self.capture(client_route, api_prefix + route_regex, sample["actions"])
            reference = capture["request"]
            if not re.fullmatch(route_regex, reference["path"]):
                raise ValueError(f"{sample['name']}：没有捕获到对应的原客户端请求")
            inputs = sample.get("inputs")
            if inputs is None:
                inputs = _input_from_capture(artifact, reference["query"])
            request = await asyncio.to_thread(preview, artifact, inputs)
            if (
                request["method"] != reference["method"]
                or request["path"] != reference["path"]
                or httpx.QueryParams(request.get("query", {})) != httpx.QueryParams(reference["query"])
            ):
                raise ValueError(f"{sample['name']}：工具请求与原客户端不一致")
            dedup = (request["path"], tuple(sorted(reference["query"].items())))
            if dedup in requests:
                raise ValueError("验收用例必须产生不同的 API 请求")
            requests.add(dedup)
            result = json.loads((await invoke(**inputs)).content[0].text)
            original = self._business_result(capture["response"])
            generated = self._business_result(result["result"])
            if result["status"] != capture["status"] or original != generated:
                raise ValueError(f"{sample['name']}：工具返回与原客户端不一致")
            response_hash = hashlib.sha256(
                json.dumps(original, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            data = original.get("data") if isinstance(original, dict) else original
            evidence.append({
                "sample": sample["name"],
                "request": reference,
                "total": data.get("total") if isinstance(data, dict) else None,
                "responseSha256": response_hash,
            })

        report = {
            "business": business,
            "tool": tool_name,
            "entry": entry,
            "clientRoute": client_route,
            "api": operation["route"],
            "cases": evidence,
            "available": "原会话下一条消息",
        }
        async with self.lock:
            directory.mkdir(parents=True, exist_ok=True)
            write_definition(directory / f"{tool_name}.json", {
                "artifact": artifact, "verification": report,
            })
        return report
