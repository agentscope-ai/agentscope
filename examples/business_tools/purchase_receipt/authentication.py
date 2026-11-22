# -*- coding: utf-8 -*-
"""ERP acceptance wiring for a profile produced from its source and OpenAPI."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx

from agentscope.app.tool_login import LoginScope
from agentscope.business._password_auth import (
    PasswordConnection,
    PasswordProfile,
    json_object,
)


@dataclass
class _Candidate:
    profile: PasswordProfile
    bad_password_checked: bool = False


class PurchaseAuthentication:
    """Stage a candidate and publish it only after original-client parity."""

    def __init__(
        self,
        source: Path,
        contract: dict,
        artifact: Path,
        connection: PasswordConnection,
        capture_browser: Callable[..., Awaitable[dict]],
    ) -> None:
        self.source = source
        self.contract = contract
        self.artifact = artifact
        self.connection = connection
        self.capture_browser = capture_browser
        self._candidates: dict[LoginScope, _Candidate] = {}

    def _validate_profile(self, profile: PasswordProfile) -> None:
        # Keep credential routing bound to the trusted OpenAPI login operation.
        for path in (
            profile.login_path,
            profile.query_path,
            profile.client_route,
        ):
            if not re.fullmatch(r"/[a-zA-Z0-9_/-]+", path) or "//" in path:
                raise ValueError("请提供源码或 OpenAPI 中的相对路由。")
        endpoints = self.contract["paths"]
        login = endpoints.get(profile.login_path, {}).get("post", {})
        if login.get("operationId") != "login" or login.get("security") != []:
            raise ValueError("登录目标应为所给 OpenAPI 的认证登录操作。")
        schema = login["requestBody"]["content"]["application/json"]["schema"]
        if "$ref" in schema:
            schema = self.contract["components"]["schemas"][
                schema["$ref"].split("/")[-1]
            ]
        schema_fields = schema["properties"]
        bound = profile.request_fields
        if (
            not set(schema["required"]) <= bound.keys() <= schema_fields.keys()
            or set(bound.values()) != {"account", "password", "tenant"}
            or len(bound) != 3
            or bound.get("password") != "password"
        ):
            raise ValueError("请求字段须与 OpenAPI 必填字段和宿主账号、账套绑定匹配。")
        if "get" not in endpoints.get(profile.query_path, {}):
            raise ValueError("验收业务必须是 OpenAPI 声明的只读 GET。")
        if not all(
            (path := (self.source / item.path).resolve()).is_relative_to(
                self.source,
            )
            and path.suffix in {".vue", ".js", ".ts", ".mjs", ".java"}
            and path.is_file()
            and item.start <= item.end <= len(path.read_text().splitlines())
            for item in profile.evidence
        ):
            raise ValueError("请提供源码中实际存在的文件及行范围。")

    def stage(self, profile: PasswordProfile) -> dict:
        """Stage a login profile and wait for the same chat's acceptance
        query."""
        self._validate_profile(profile)
        scope = self.connection.broker.current_scope()
        self._candidates[scope] = _Candidate(profile)
        return {
            "status": "staged",
            "next": (
                "直接调用 query_recent_purchase_receipts 开始验收。若弹窗出现，请等待用户在弹窗内登录。对"
                "照通过后自动注册。"
            ),
        }

    def published(self) -> PasswordProfile | None:
        """Read the published profile, if one has passed parity."""
        if self.artifact.exists():
            profile = PasswordProfile.model_validate_json(
                self.artifact.read_bytes(),
            )
            self._validate_profile(profile)
            return profile
        return None

    def registered_read_auth(self) -> httpx.Auth | None:
        """Use the published service login profile for registered queries."""
        profile = self.published()
        return self.connection.read_auth(profile) if profile else None

    async def query(self, limit: int = 5) -> dict:
        """Read recent receipts and complete pending adapter acceptance."""
        if limit < 1:
            raise ValueError("单据条数必须大于零。")
        scope = self.connection.broker.current_scope()
        candidate = self._candidates.get(scope)
        profile = candidate.profile if candidate else self.published()
        if profile is None:
            raise ValueError("请先从源码和 API 生成并提交服务登录适配器。")
        reference = None

        def mark_bad_password() -> None:
            if candidate:
                candidate.bad_password_checked = True

        async def prepare(auth: dict) -> dict:
            nonlocal reference
            reference = await self.capture_browser(
                profile.client_route,
                "/api" + profile.query_path,
                [],
                login=auth["session"],
                login_acceptance=True,
            )
            return reference["request"]["query"]

        response = await self.connection.read(
            profile,
            {"current": 1, "size": limit},
            on_bad_password=mark_bad_password,
            prepare=prepare if candidate else None,
        )
        response.raise_for_status()
        result = json_object(response)
        if result.get("code") != 200:
            raise ValueError(result.get("message") or "ERP 采购单查询失败。")
        data = result["data"]
        summary = {
            "total": data["total"],
            "records": [
                {
                    key: row.get(key)
                    for key in (
                        "billNo",
                        "billDate",
                        "createdTime",
                        "supplierName",
                        "goodsTotalAmount",
                    )
                }
                for row in data["records"][:limit]
            ],
        }
        if candidate:
            summary["authentication"] = self._publish(
                candidate,
                scope,
                reference,
                data,
            )
        return summary

    def _publish(
        self,
        candidate: _Candidate,
        scope: LoginScope,
        reference: dict | None,
        data: dict,
    ) -> dict:
        profile = candidate.profile
        if (
            not reference
            or reference["request"]["path"] != profile.query_path
            or reference["response"].get("code") != 200
            or reference["response"]["data"] != data
        ):
            raise ValueError("生成工具与原客户端同条件查询结果不一致，适配器未注册。")
        for case in reference["authCases"]:
            response = httpx.Response(case["status"], json=case["body"])
            if profile.session_expired(response) != case["redirected"]:
                raise ValueError(f"失效规则与原客户端不一致（{case['name']}），适配器未注册。")
            if case["redirected"] != case["expectedRedirect"]:
                raise ValueError(f"原客户端未复现预期分支（{case['name']}），适配器未注册。")
        if not candidate.bad_password_checked:
            raise ValueError("尚未验证旧凭证被拒，适配器未注册。")
        temp = self.artifact.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temp.write_text(profile.model_dump_json(indent=2) + "\n")
            temp.replace(self.artifact)
        finally:
            temp.unlink(missing_ok=True)
        self._candidates.pop(scope, None)
        return {
            "status": "registered",
            "same_request_and_result": True,
            "old_password_rejected": True,
            "original_client_branches_checked": [
                case["name"] for case in reference["authCases"]
            ],
        }
