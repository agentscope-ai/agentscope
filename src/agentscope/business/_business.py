"""Compile a client action and an OpenAPI operation into an AgentScope tool."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from urllib.parse import quote, urlsplit

import httpx
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from agentscope.tool import FunctionTool

HERE = Path(__file__).resolve().parent
FORMAT = "agentscope.business-tool.v1"


def node(script: str, *args: str, data: dict | None = None) -> dict:
    """Run the local parser or interpreter, never the selected client."""
    with TemporaryFile(mode="w+", encoding="utf-8") as payload:
        if data is not None:
            json.dump(data, payload, allow_nan=False)
            payload.seek(0)
        result = subprocess.run(
            ["node", str(HERE / script), *args],
            stdin=payload,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Node process failed")
    return json.loads(result.stdout)


def dereference(value, document, trail=()):
    """Resolve local JSON pointers so the artifact works offline."""
    if isinstance(value, list):
        return [dereference(item, document, trail) for item in value]
    if not isinstance(value, dict):
        return value
    if "$dynamicRef" in value or "$id" in value:
        raise ValueError(
            "Schema resource IDs and dynamic references are unsupported"
        )
    if "$ref" in value:
        ref = value["$ref"]
        if len(value) != 1 or not ref.startswith("#/") or ref in trail:
            raise ValueError(
                f"Expected a standalone, acyclic local reference: {ref}"
            )
        target = document
        for key in ref[2:].split("/"):
            target = target[key.replace("~1", "/").replace("~0", "~")]
        return dereference(target, document, (*trail, ref))
    return {k: dereference(v, document, trail) for k, v in value.items()}


def openapi_schema(schema, version):
    """Translate the OpenAPI 3.0 nullable and exclusive bound keywords."""
    if not version.startswith("3.0.") or not isinstance(schema, dict):
        return schema
    schema = dict(schema)
    if schema.pop("nullable", False) and isinstance(schema.get("type"), str):
        schema["type"] = [schema["type"], "null"]
    for limit in ("minimum", "maximum"):
        exclusive = "exclusive" + limit.capitalize()
        if isinstance(schema.get(exclusive), bool) and schema.pop(exclusive):
            schema[exclusive] = schema.pop(limit)
    if "properties" in schema:
        schema["properties"] = {
            k: openapi_schema(v, version)
            for k, v in schema["properties"].items()
        }
    for key in ("items", "not", "additionalProperties"):
        if key in schema:
            schema[key] = openapi_schema(schema[key], version)
    for key in ("allOf", "oneOf", "anyOf"):
        if key in schema:
            schema[key] = [openapi_schema(v, version) for v in schema[key]]
    return schema


def json_schema(content: dict, version: str) -> dict | bool:
    if "application/json" not in content and "*/*" not in content:
        raise ValueError("This operation requires a JSON representation")
    schema = content.get("application/json", content.get("*/*")).get(
        "schema", {}
    )
    schema = openapi_schema(schema, version)
    Draft202012Validator.check_schema(schema)
    return schema


def bind_operation(document: dict, endpoint: dict) -> dict:
    """Match verb and route shape and retain the applicable wire contracts."""
    if not document.get("openapi", "").startswith(("3.0.", "3.1.")):
        raise ValueError("An OpenAPI 3.x JSON document is required")
    source_route = re.sub(r"\{\d+\}", "{}", endpoint["template"])
    verb = endpoint["method"].lower()
    candidates = [
        (route, item)
        for route, item in document["paths"].items()
        if verb in item and re.sub(r"\{[^/{}]+\}", "{}", route) == source_route
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Expected one OpenAPI match for {verb.upper()} {source_route}, found {len(candidates)}"
        )
    route, item = candidates[0]
    op = dereference(item[verb], document)
    name = op.get("operationId", "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("A tool-compatible operationId is required")

    parameters = {}
    for param in [
        *dereference(item.get("parameters", []), document),
        *op.get("parameters", []),
    ]:
        location, key = param["in"], param["name"]
        if location not in ("query", "path"):
            if param.get("required"):
                raise ValueError(
                    f"Unsupported required {location} parameter: {key}"
                )
            continue
        style = "form" if location == "query" else "simple"
        schema = openapi_schema(param["schema"], document["openapi"])
        if (
            schema.get("type")
            not in ("string", "integer", "number", "boolean")
            or param.get("style", style) != style
            or param.get("allowReserved")
        ):
            raise ValueError(f"Unsupported parameter serialization: {key}")
        Draft202012Validator.check_schema(schema)
        parameters[(location, key)] = {**param, "schema": schema}
    path_names = re.findall(r"\{([^{}]+)\}", route)
    if set(path_names) != {k for loc, k in parameters if loc == "path"}:
        raise ValueError("Path template and parameter definitions disagree")
    responses = {}
    for status, response in op["responses"].items():
        if status.startswith("2") or status == "default":
            responses[status] = (
                json_schema(response["content"], document["openapi"])
                if response.get("content")
                else None
            )
    if not responses:
        raise ValueError("OpenAPI does not declare a successful response")
    body = op.get("requestBody")
    return {
        "name": name,
        "description": op.get("description") or op.get("summary") or name,
        "route": route,
        "pathNames": path_names,
        "parameters": list(parameters.values()),
        "bodySchema": json_schema(body["content"], document["openapi"])
        if body
        else None,
        "bodyRequired": bool(body and body.get("required")),
        "responses": responses,
    }


def discover(
    source: Path, openapi: Path, http_client: str | None = None
) -> dict:
    """Find supported actions with a matching API and expose skipped candidates."""
    scan = node(
        "extract.mjs",
        str(source.resolve()),
        "--discover",
        "",
        json.dumps({"httpClient": http_client}),
    )
    document = json.loads(openapi.read_bytes())
    matches = []
    for action in scan["actions"]:
        try:
            op = bind_operation(document, action["endpoint"])
        except (KeyError, ValueError) as exc:
            scan["diagnostics"].append(f"{action['entry']}: {exc}")
            continue
        matches.append(
            {
                "entry": action["entry"],
                "tool": op["name"],
                "route": op["route"],
                "calls": action["calls"],
                "method": action["endpoint"]["method"],
            }
        )
    helpers = {f for match in matches for f in match["calls"]}
    return {
        "actions": [m for m in matches if m["entry"] not in helpers],
        "diagnostics": [
            d
            for d in scan["diagnostics"]
            if d.split(": ", 1)[0] not in helpers
        ],
    }


def omit_computed_requirement(schema, path, field):
    """The caller may omit fields that the compiled plan will overwrite."""
    if not isinstance(schema, dict):
        return
    for combination in ("allOf", "anyOf", "oneOf"):
        for child in schema.get(combination, []):
            omit_computed_requirement(child, path, field)
    if path:
        child = schema.get("properties", {}).get(path[0])
        omit_computed_requirement(child, path[1:], field)
    elif "items" in schema:
        omit_computed_requirement(schema["items"], (), field)
    else:
        if field in schema.get("required", []):
            schema["required"].remove(field)
        if field in schema.get("properties", {}):
            schema["properties"][field] = {}


def build(
    source: Path, entry: str | None, openapi: Path, profile: dict | None = None
) -> dict:
    """Build a portable tool definition with source and API evidence."""
    profile = profile or {}
    allowed = {
        "entry",
        "httpClient",
        "derive",
        "success",
        "responseCorrection",
        "responseCorrections",
        "openapiJavaObjectMaps",
    }
    if unknown := set(profile) - allowed:
        raise ValueError(f"Unrecognized profile fields: {sorted(unknown)}")
    if entry and profile.get("entry", entry) != entry:
        raise ValueError("Profile and command select different actions")
    entry = entry or profile.get("entry")
    if not entry:
        scan = discover(source, openapi, profile.get("httpClient"))
        candidates = [m["entry"] for m in scan["actions"]]
        if len(candidates) != 1:
            raise ValueError(
                f"Expected one action, found {candidates}. Use discover or specify --entry."
            )
        entry = candidates[0]
    file, separator, symbol = entry.rpartition("#")
    if not separator or not file or not symbol:
        raise ValueError("Use --entry relative/file#function")
    plan = node(
        "extract.mjs", str(source.resolve()), file, symbol, json.dumps(profile)
    )
    raw = openapi.read_bytes()
    document = json.loads(raw)
    if profile.get("openapiJavaObjectMaps"):
        # Spring's Map<String, Object> components describe arbitrary Java
        # values as JSON objects. The values may also be scalars or null.
        def normalize_map_values(schema):
            if isinstance(schema, dict):
                if schema.get("type") == "object" and schema.get(
                    "additionalProperties"
                ) == {"type": "object"}:
                    schema["additionalProperties"] = True
                for value in schema.values():
                    normalize_map_values(value)
            elif isinstance(schema, list):
                for value in schema:
                    normalize_map_values(value)

        for name, schema in document.get("components", {}).get("schemas", {}).items():
            if name.endswith("MapStringObject"):
                normalize_map_values(schema)
    operation = bind_operation(document, plan["endpoint"])
    corrections = list(profile.get("responseCorrections", []))
    if "responseCorrection" in profile:
        corrections.append(profile["responseCorrection"])
    for correction in corrections:
        if not correction.get("reason"):
            raise ValueError("A response schema correction requires a reason")
        parent = operation["responses"][correction["status"]]
        parts = correction["schemaPath"]
        if (
            not isinstance(parts, list)
            or len(parts) < 2
            or parts[-2] != "properties"
        ):
            raise ValueError(
                "A correction must select one declared response property"
            )
        for step in parts[:-1]:
            parent = parent[step]
        if parts[-1] not in parent:
            raise ValueError(
                "Correction refers to an undeclared response property"
            )
        Draft202012Validator.check_schema(correction["schema"])
        parent[parts[-1]] = correction["schema"]
    params = plan["functions"][plan["entry"]]["params"]
    schema = {
        "type": "object",
        "properties": {p["name"]: p["schema"] for p in params},
        "required": [p["name"] for p in params if not p["optional"]],
        "additionalProperties": False,
    }
    # An untyped JS adapter can still expose precise tool arguments from the
    # matched operation when it passes those arguments straight through.
    result = plan["functions"][plan["entry"]]["steps"][-1]["return"]
    if result.get("request"):

        def bind_argument(expression, contract):
            name = expression.get("variable")
            if name in schema["properties"] and not schema["properties"][name]:
                schema["properties"][name] = contract

        if query := result.get("query"):
            query_parameters = [
                p for p in operation["parameters"] if p["in"] == "query"
            ]
            bind_argument(
                query,
                {
                    "type": "object",
                    "properties": {
                        p["name"]: p["schema"] for p in query_parameters
                    },
                    "required": [
                        p["name"] for p in query_parameters if p.get("required")
                    ],
                    "additionalProperties": False,
                },
            )

        if operation["bodySchema"] is not None:
            expression = result.get("body", {})
            contract = deepcopy(operation["bodySchema"])
            for rule in plan["derivations"]:
                argument, *path = rule["each"].split(".")
                if argument == expression.get("variable"):
                    omit_computed_requirement(contract, path, rule["field"])
            bind_argument(expression, contract)
        path_schemas = {
            p["name"]: p["schema"]
            for p in operation["parameters"]
            if p["in"] == "path"
        }
        for key, expression in zip(
            operation["pathNames"], result["bindings"], strict=True
        ):
            bind_argument(expression, path_schemas[key])
    Draft202012Validator.check_schema(schema)
    return {
        "format": FORMAT,
        "operation": operation,
        "inputSchema": schema,
        "plan": plan,
        "profile": profile,
        "apiEvidence": {
            "file": openapi.name,
            "pointer": "/paths/"
            + operation["route"].replace("~", "~0").replace("/", "~1")
            + "/"
            + plan["endpoint"]["method"].lower(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
    }


def validate(schema, value, label: str) -> None:
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            value
        )
    except ValidationError as err:
        raise ValueError(f"{label} {err.json_path}: {err.message}") from err


def preview(artifact: dict, inputs: dict) -> dict:
    if artifact.get("format") != FORMAT:
        raise ValueError("Unsupported tool definition")
    validate(artifact["inputSchema"], inputs, "Tool input")
    req = node("prepare.mjs", data={"plan": artifact["plan"], "input": inputs})
    op = artifact["operation"]
    wire = {
        "path": dict(zip(op["pathNames"], req.pop("pathValues"), strict=True)),
        "query": req.get("query", {}),
    }
    if not isinstance(wire["query"], dict):
        raise TypeError("Query arguments must be an object")
    declared = {p["name"] for p in op["parameters"] if p["in"] == "query"}
    if extra := set(wire["query"]) - declared:
        raise ValueError(
            f"Query parameters absent from OpenAPI: {sorted(extra)}"
        )
    for param in op["parameters"]:
        key, location = param["name"], param["in"]
        if key in wire[location]:
            validate(param["schema"], wire[location][key], key)
        elif param.get("required"):
            raise ValueError(f"Missing {location} parameter {key}")
    if "body" in req:
        if op["bodySchema"] is None:
            raise ValueError("OpenAPI does not declare a request body")
        validate(op["bodySchema"], req["body"], "Request body")
    elif op["bodyRequired"]:
        raise ValueError("Missing required request body")
    return req


async def load_editor_lists(artifact, inputs, client, base_url, headers):
    program = artifact["plan"].get("editorProgram", {})
    lookups = program.get("lookups", {})
    supplied = inputs.get("editorState", {})
    state = {
        name: value
        for name, value in supplied.items()
        if not lookups.get(name, {}).get("authoritative")
    }
    if not lookups or all(name in state for name in lookups):
        return inputs
    validate(artifact["inputSchema"], inputs, "Tool input")
    requests = await asyncio.to_thread(
        node,
        "prepare.mjs",
        data={
            "plan": artifact["plan"],
            "input": {**inputs, "editorState": state},
            "lookupOnly": True,
        },
    )

    async def read(route, query):
        response = await client.get(
            base_url.rstrip("/") + route,
            params=query,
            headers=headers,
        )
        response.raise_for_status()
        rows = response.json()
        success = artifact.get("profile", {}).get("success")
        if success and (
            not isinstance(rows, dict)
            or rows.get(success["field"]) != success["equals"]
        ):
            raise ValueError(f"ERP lookup failed for {route}")
        if (key := program.get("responseProperty")) and isinstance(rows, dict):
            rows = rows.get(key)
        return rows

    async def resolve(name, lookup):
        if "templates" in lookup:
            columns = {}
            for resource, template in lookup["templates"].items():
                codes = lookup["moduleCodes"]
                match = re.fullmatch(r"(/[^{}]+)\{\w+\}", template)
                if not match or template.startswith("//"):
                    raise ValueError(
                        f"Invalid column lookup template: {template}"
                    )
                paths = [match[1] + quote(code, safe="") for code in codes]
                values = await asyncio.gather(
                    *(read(path, {}) for path in paths)
                )
                columns[resource] = dict(zip(codes, values, strict=True))
            return name, columns
        request = requests[name]
        route = request["path"]
        if (
            route not in lookup["paths"]
            or not route.startswith("/")
            or route.startswith("//")
        ):
            raise ValueError(f"Undocumented editor lookup for {name}")
        rows = await read(route, request["query"])
        if lookup.get("kind") == "object":
            if not isinstance(rows, dict):
                raise TypeError(
                    f"Expected an ERP configuration map for {name}"
                )
            return name, rows
        if lookup.get("records") and isinstance(rows, dict):
            rows = rows["records"]
        if not isinstance(rows, list):
            raise TypeError(f"Expected an ERP list for {name}")
        return name, rows

    resolved = await asyncio.gather(
        *(
            resolve(name, lookup)
            for name, lookup in lookups.items()
            if name not in state
        )
    )
    return {**inputs, "editorState": {**state, **dict(resolved)}}


def validate_api_base_url(base_url: str, *, authenticated: bool) -> None:
    """Require HTTPS for credentials sent beyond the loopback interface."""
    url = urlsplit(base_url)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
    ):
        raise ValueError("Expected an HTTP(S) API base URL")
    if (
        authenticated
        and url.scheme != "https"
        and url.hostname not in ("localhost", "127.0.0.1", "::1")
    ):
        raise ValueError("Use HTTPS when sending service credentials")


def as_tool(
    artifact: dict, base_url: str, token: str | None = None, *, auth: httpx.Auth | None = None
) -> FunctionTool:
    """Load a built definition for use in Toolkit(tools=[...])."""
    validate_api_base_url(base_url, authenticated=bool(token or auth))
    op = artifact["operation"]

    async def invoke(**inputs):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(
            timeout=15, follow_redirects=False, trust_env=False, auth=auth
        ) as client:
            inputs = await load_editor_lists(
                artifact, inputs, client, base_url, headers
            )
            req = await asyncio.to_thread(preview, artifact, inputs)
            kwargs = {"json": req["body"]} if "body" in req else {}
            response = await client.request(
                req["method"],
                base_url.rstrip("/") + req["path"],
                params=req.get("query"),
                headers=headers,
                **kwargs,
            )
        response.raise_for_status()
        status = str(response.status_code)
        contract = op["responses"]
        key = next(
            (k for k in (status, "2XX", "default") if k in contract), None
        )
        if key is None:
            raise ValueError(f"Undeclared response HTTP {status}")
        if contract[key] is None:
            if response.content:
                raise ValueError(f"Expected empty response for HTTP {status}")
            payload = None
        else:
            media = (
                response.headers.get("content-type", "")
                .split(";")[0]
                .strip()
                .lower()
            )
            if media != "application/json":
                raise ValueError(
                    f"Expected application/json, received {media}"
                )
            payload = response.json()
            success = artifact.get("profile", {}).get("success")
            if success and (
                not isinstance(payload, dict)
                or payload.get(success["field"]) != success["equals"]
            ):
                raise ValueError(
                    f"Business request failed: {payload.get('message', '') if isinstance(payload, dict) else 'invalid envelope'}"
                )
            validate(contract[key], payload, "Response")
        result = {"status": response.status_code, "result": payload}
        if response.extensions.get("agentscope_resumed_after_login"):
            result["authentication"] = {"status": "resumed_after_login"}
        return result

    read_only = artifact["plan"]["endpoint"]["method"] == "GET"
    return FunctionTool(
        invoke,
        name=op["name"],
        description=op["description"],
        input_schema=artifact["inputSchema"],
        is_read_only=read_only,
        is_concurrency_safe=read_only,
    )


def write_definition(path: Path, artifact: dict) -> None:
    contents = (
        json.dumps(artifact, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n"
    )
    with TemporaryDirectory(dir=path.parent) as directory:
        tmp = Path(directory) / "definition.json"
        tmp.write_text(contents, encoding="utf8")
        tmp.replace(path)


def main() -> None:
    from ._workflow import WORKFLOW_FORMAT, generate

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in (
        "discover",
        "generate",
        "build",
        "check",
        "inspect",
        "preview",
        "run",
    ):
        cmd = sub.add_parser(command)
        if command != "discover":
            cmd.add_argument("artifact", type=Path)
        if command in ("discover", "generate", "build", "check"):
            cmd.add_argument("--source", type=Path, required=True)
            if command == "discover":
                cmd.add_argument(
                    "--http-client",
                    help="Module import used as the Axios wrapper",
                )
            elif command == "generate":
                cmd.add_argument("--business", required=True)
            else:
                cmd.add_argument("--entry")
                cmd.add_argument("--profile", type=Path)
            cmd.add_argument("--openapi", type=Path, required=True)
        if command in ("preview", "run"):
            cmd.add_argument(
                "--input", required=True, help="Tool input as JSON"
            )
        if command in ("inspect", "preview", "run"):
            cmd.add_argument("--tool", choices=("save", "detail"))
        if command == "run":
            cmd.add_argument("--base-url", required=True)
    args = parser.parse_args()
    if args.command == "generate":
        bundle = generate(args.source, args.openapi, args.business)
        write_definition(args.artifact, bundle)
        print(bundle["discovery"]["handler"])
        return
    if args.command == "discover":
        print(
            json.dumps(
                discover(args.source, args.openapi, args.http_client),
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    if args.command in ("build", "check"):
        existing = (
            json.loads(args.artifact.read_bytes())
            if args.command == "check"
            else {}
        )
        if existing.get("format") == WORKFLOW_FORMAT:
            current = generate(args.source, args.openapi, existing["business"])
            if existing != current:
                raise ValueError(
                    "Source or API changed. Regenerate the workflow."
                )
            print(current["discovery"]["handler"])
            return
        profile = (
            json.loads(args.profile.read_bytes())
            if args.profile
            else existing.get("profile")
        )
        entry = args.entry or existing.get("plan", {}).get("entry")
        compiled = build(args.source, entry, args.openapi, profile)
        if args.command == "build":
            write_definition(args.artifact, compiled)
        elif compiled != existing:
            raise ValueError(
                "Source or API changed. Inspect the differences and rebuild the tool."
            )
        print(compiled["operation"]["name"])
        return
    artifact = json.loads(args.artifact.read_text())
    if artifact.get("format") == WORKFLOW_FORMAT:
        if args.command == "inspect" and not args.tool:
            print(
                json.dumps(artifact["discovery"], ensure_ascii=False, indent=2)
            )
            return
        if not args.tool:
            raise ValueError("Select --tool save or --tool detail")
        artifact = artifact["tools"][args.tool]
    if args.command == "inspect":
        plan = artifact["plan"]
        api = artifact["apiEvidence"]
        print(
            f"OpenAPI {api['file']}#{api['pointer']}  SHA-256 {api['sha256']}"
        )
        print(
            artifact["operation"]["name"],
            plan["endpoint"]["method"],
            artifact["operation"]["route"],
        )
        for name, fn in plan["functions"].items():
            print(name)
            for e in [
                *fn["constants"],
                *(step["evidence"] for step in [*fn["params"], *fn["steps"]]),
            ]:
                print(f"  {e['file']}:{e['start']}-{e['end']}  {e['code']}")
        for ref in plan["endpoint"]["transport"]:
            print(
                f"  {ref['file']}:{ref['start']}-{ref['end']}  {ref['code']}"
            )
        if correction := artifact["profile"].get("responseCorrection"):
            print("Response contract:", correction["reason"])
        for correction in artifact["profile"].get("responseCorrections", []):
            print("Response contract:", correction["reason"])
    elif args.command == "preview":
        print(
            json.dumps(
                preview(artifact, json.loads(args.input)), ensure_ascii=False
            )
        )
    else:
        tool = as_tool(
            artifact, args.base_url, os.getenv("BUSINESS_API_TOKEN")
        )
        response = asyncio.run(tool(**json.loads(args.input)))
        print(response.content[0].text)
