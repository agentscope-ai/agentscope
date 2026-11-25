# -*- coding: utf-8 -*-
"""Infer a document workflow from the client and its API contract."""

from collections.abc import Iterator
import asyncio
import hashlib
import json
import re
from pathlib import Path

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool

from ._business import (
    bind_operation,
    build,
    node,
    omit_computed_requirement,
    write_definition,
)

WORKFLOW_FORMAT = "agentscope.business-workflow.v1"


def property_paths(
    schema: dict,
    prefix: tuple = (),
) -> Iterator[tuple[str, dict, tuple]]:
    """Yield property locations including OpenAPI allOf wrappers."""
    if not isinstance(schema, dict):
        return
    for key, child in schema.get("properties", {}).items():
        yield key, child, (*prefix, "properties", key)
    for index, child in enumerate(schema.get("allOf", [])):
        yield from property_paths(child, (*prefix, "allOf", index))


def properties(schema: dict) -> dict:
    """Collect properties declared in a schema and its compositions."""
    return {key: value for key, value, _ in property_paths(schema)}


def editor_body_schema(
    schema: dict,
    defaults: list[dict],
    program: dict,
) -> None:
    """Retain wire nulls produced by form defaults and relation cleanup."""
    nulls = {(d["section"], d["field"]) for d in defaults}
    nulls.update(tuple(item["path"]) for item in program["nullableRelations"])

    def visit(item: object, path: tuple = ()) -> None:
        if not isinstance(item, dict):
            return
        for key, child in item.get("properties", {}).items():
            field = (*path, key)
            if field in nulls and key not in item.get("required", ()):
                kind = child.get("type") if isinstance(child, dict) else None
                if isinstance(kind, str) and kind != "null":
                    child["type"] = [kind, "null"]
            visit(child, field)
        for member in item.get("allOf", []):
            visit(member, path)
        visit(item.get("items"), path)

    visit(schema)


def select_pair(document: dict, business: str) -> tuple[str, str]:
    """Find the unique create and detail routes for this business."""
    pairs = []
    for route, methods in document["paths"].items():
        save = methods.get("post", {})
        labels = [*save.get("tags", []), save.get("summary", "")]
        if not save.get("requestBody") or not any(
            business.casefold() in label.casefold() for label in labels
        ):
            continue
        for detail_route, item in document["paths"].items():
            parent, _, tail = detail_route.rpartition("/")
            if (
                parent == route
                and re.fullmatch(r"\{[^{}]+\}", tail)
                and "get" in item
            ):
                pairs.append((route, detail_route))
    if len(pairs) != 1:
        raise ValueError(
            f"Expected one documented create/read pair for {business!r}, "
            f"found {pairs}",
        )
    return pairs[0]


def approval_defaults(detail: dict, defaults: list[dict]) -> Iterator[dict]:
    """A null editor default also needs an API-documented later obligation."""
    nullable = {(d["section"], d["field"]): d["evidence"] for d in defaults}
    for status, schema in detail["responses"].items():
        if not isinstance(schema, dict):
            continue
        for _, envelope, ep in property_paths(schema):
            for section, model, sp in property_paths(envelope, ep):
                for key, field, fp in property_paths(model, sp):
                    source = nullable.get((section, key))
                    description = field.get("description", "")
                    if (
                        source
                        and field.get("type") == "number"
                        and re.search(
                            r"审核时必填|审批时必填|required (?:on|at) approval",
                            description,
                            re.IGNORECASE,
                        )
                    ):
                        yield {
                            "status": status,
                            "schemaPath": list(fp),
                            "schema": {**field, "type": ["number", "null"]},
                            "reason": (
                                "Draft default is null; the API requires "
                                "this field at approval."
                            ),
                            "source": source,
                            "apiDescription": description,
                        }


def generate(source: Path, openapi: Path, business: str) -> dict:
    """Discover, compile and return a bundle. No sample or profile is used."""
    business = business.strip()
    if not business:
        raise ValueError("A business name is required")
    raw_api = openapi.read_bytes()
    document = json.loads(raw_api)
    save_route, detail_route = select_pair(document, business)
    spec: dict = {
        "save": {"route": save_route, "method": "POST"},
        "detail": {"route": detail_route, "method": "GET"},
    }
    ops = {
        role: bind_operation(
            document,
            {
                "method": op["method"],
                "template": re.sub(r"\{[^{}]+\}", "{0}", op["route"]),
            },
        )
        for role, op in spec.items()
    }
    spec["reads"] = [
        path for path, methods in document["paths"].items() if "get" in methods
    ]
    spec["maps"] = []
    definitions = document.get("components", {}).get("schemas", {})
    for path in spec["reads"]:
        response = (
            document["paths"][path]["get"].get("responses", {}).get("200", {})
        )
        for media in response.get("content", {}).values():
            schema = media.get("schema", {})
            envelope = definitions.get(
                schema.get("$ref", "").rsplit("/", 1)[-1],
                schema,
            )
            if any(
                field.get("type") == "object"
                and "additionalProperties" in field
                for field in envelope.get("properties", {}).values()
            ):
                spec["maps"].append(path)
    spec["rows"] = {
        key: [
            k
            for k, f in properties(value.get("items", {})).items()
            if f.get("type") in ("number", "integer") and not f.get("readOnly")
        ]
        for key, value in properties(ops["save"]["bodySchema"]).items()
        if value.get("type") == "array"
    }
    inferred = node("extract.mjs", str(source.resolve()), "--infer", data=spec)
    report = inferred["report"]
    for role, op in ops.items():
        # Use the documented success discriminator when the API wraps errors in
        # HTTP 200.
        for key, field in properties(op["responses"].get("200") or {}).items():
            match = re.search(
                r"(\d+)\s*(?:表示成功|means success)",
                field.get("description", ""),
                re.IGNORECASE,
            )
            if match and field.get("type") == "integer":
                inferred[role]["success"] = {
                    "field": key,
                    "equals": int(match[1]),
                }

    prop = inferred.get("resultProperty")
    response = ops["save"]["responses"].get("200") or {}
    saved_type = properties(response).get(prop, {})
    identifier = next(
        p["schema"] for p in ops["detail"]["parameters"] if p["in"] == "path"
    )
    # Only reconcile a generic envelope when the client's data flow and the
    # documented read parameter agree on the concrete return value.
    if (
        saved_type.get("type") == "object"
        and identifier["type"] != "object"
        and re.search(
            r"\bID\b",
            saved_type.get("description", ""),
            re.IGNORECASE,
        )
    ):
        inferred["save"]["responseCorrections"] = [
            {
                "status": "200",
                "schemaPath": ["properties", prop],
                "schema": dict(identifier),
                "reason": (
                    "The client passes the create result into the documented "
                    "detail path parameter."
                ),
                "source": report["readArgument"],
                "apiDescription": saved_type["description"],
            },
        ]
    inferred["detail"]["responseCorrections"] = list(
        approval_defaults(ops["detail"], inferred["defaults"]),
    )
    artifacts = {
        role: build(source, None, openapi, inferred[role]) for role in ops
    }
    save = artifacts["save"]
    if program := inferred.get("program"):
        program["responseProperty"] = prop
        save["plan"]["editorProgram"] = program
        save["plan"]["hashes"].update(program["hashes"])
        editor_body_schema(
            save["operation"]["bodySchema"],
            inferred["defaults"],
            program,
        )
        editor_body_schema(
            save["inputSchema"]["properties"][program["argument"]],
            inferred["defaults"],
            program,
        )
        if program["state"]:
            save["inputSchema"]["properties"]["editorState"] = {
                "type": "object",
                "additionalProperties": False,
                "properties": {name: {} for name in program["state"]},
                "description": (
                    "Loaded editor reference data for the same ERP session."
                ),
            }
        report["editor"] = {
            "builder": program["builder"],
            "hooks": program["hooks"],
            "guards": program["guards"],
            "requiresClient": program["remaining"],
        }
        report["editor"].update(
            after=program["after"],
            watchers=[w["name"] for w in program["watches"]],
            referenceLists=list(program["lookups"]),
        )
        report["wholeFunctionFields"] = []
        unresolved = []
        for field in report["omitted"]:
            if any(
                c["key"] == field["each"] and c["name"] == field["owner"]
                for c in program["calculators"]
            ):
                report["wholeFunctionFields"].append(
                    {key: field[key] for key in ("each", "field", "evidence")},
                )
                omit_computed_requirement(
                    save["inputSchema"]["properties"][program["argument"]],
                    [field["each"]],
                    field["field"],
                )
            else:
                unresolved.append(field)
        report["omitted"] = unresolved
    elif inferred.get("programError"):
        report["editorDiagnostic"] = inferred["programError"]
    if (
        artifacts["save"]["plan"]["hashes"].get(report["createCall"]["file"])
        != inferred["pageHash"]
    ):
        raise ValueError(
            "Client changed during discovery. Run generation again.",
        )
    for role, artifact in artifacts.items():
        if (
            artifact["apiEvidence"]["sha256"]
            != hashlib.sha256(raw_api).hexdigest()
        ):
            raise ValueError(
                "OpenAPI changed during discovery. Run generation again.",
            )
        if artifact["operation"]["route"] != spec[role]["route"]:
            raise ValueError(
                "Compiled route does not match the discovered operation",
            )
    if report["omitted"]:
        skipped = ", ".join(f["field"] for f in report["omitted"])
        artifacts["save"]["operation"][
            "description"
        ] += f" 当前工具尚未实现这些客户端派生字段的计算：{skipped}。"
    return {
        "format": WORKFLOW_FORMAT,
        "business": business,
        "discovery": report,
        "tools": artifacts,
    }


def discovery_tool(source: Path, openapi: Path, output: Path) -> FunctionTool:
    """Give an AgentScope agent a source/API pair and a destination for its
    tools."""

    async def generate_business_tools(business: str) -> dict:
        bundle = await asyncio.to_thread(generate, source, openapi, business)
        write_definition(output, bundle)
        report = bundle["discovery"]
        return {
            "artifact": str(output),
            "handler": report["handler"],
            "tools": [
                tool["operation"]["name"] for tool in bundle["tools"].values()
            ],
            "fields": [
                f["field"]
                for f in [
                    *report["fields"],
                    *report.get("wholeFunctionFields", []),
                ]
            ],
            "clientOnlyChecks": report.get("editor", {}).get(
                "requiresClient",
                [],
            ),
            "inputs": sorted(
                {key for f in report["fields"] for key in f["inputs"]},
            ),
            "unresolved": [f["field"] for f in report["omitted"]],
        }

    return FunctionTool(
        generate_business_tools,
        name="generate_business_tools",
        description="根据已提供的源码和 OpenAPI 发现指定单据的新增、回查和明细计算，生成可试运行的工具。",
        permission=PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=(
                "Generate a local definition at the host-selected output "
                "path."
            ),
        ),
        input_schema={
            "type": "object",
            "required": ["business"],
            "properties": {"business": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
    )
