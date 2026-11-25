"""Compare a generated request or persisted receipt to the original client."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime

import httpx

from agentscope.business import preview


def compare(expected, actual, path="$"):
    """Report the first difference, including omissions and JSON type changes."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        if expected.keys() != actual.keys():
            raise ValueError(
                f"{path}: missing {sorted(expected.keys() - actual.keys())}, "
                f"extra {sorted(actual.keys() - expected.keys())}"
            )
        for key in expected:
            compare(expected[key], actual[key], f"{path}.{key}")
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            raise ValueError(
                f"{path}: length {len(actual)}, expected {len(expected)}"
            )
        for index, (left, right) in enumerate(
            zip(expected, actual, strict=True)
        ):
            compare(left, right, f"{path}[{index}]")
    elif expected != actual or isinstance(expected, bool) != isinstance(
        actual, bool
    ):
        raise ValueError(f"{path}: expected {expected!r}, received {actual!r}")


def check_cases(bundle, reference):
    save, read = (bundle["tools"][role] for role in ("save", "detail"))
    report = bundle["discovery"]
    page = report["handler"].rsplit("#", 1)[0]
    compare(
        reference["pageSha256"],
        save["plan"]["hashes"][page],
        "reference editor SHA-256",
    )
    if "editorProgram" not in save["plan"]:
        raise ValueError(
            "Incomplete editor program: "
            + report.get("editorDiagnostic", "missing")
        )
    names = [c["name"] for c in reference["cases"]]
    expected_cases = {
        "base",
        "quantity",
        "rounding",
        "edited",
        "piecework",
        "payment",
        "empty",
        "bad-payment",
        "no-color",
        "closed",
        "approved",
        "void",
        "required-header",
        "required-detail",
        "required-batch",
        "required-complete",
    }
    compare(sorted(expected_cases), sorted(names), "reference cases")
    results = []
    for case in reference["cases"]:
        name, expected = case["name"], case["request"]
        try:
            actual = preview(save, case["input"])
        except ValueError as exc:
            if expected is not None:
                raise
            compare(case["message"], str(exc), name)
            compare([], case["reads"], name + ".reads")
            results.append({"case": name, "rejected": str(exc)})
        else:
            if expected is None:
                raise ValueError(
                    f"{name}: the client blocked saving but the tool accepted it"
                )
            compare(expected, actual, name)
            # The reference API returns test ID 918 to the original save handler.
            read_req = preview(read, {read["operation"]["pathNames"][0]: 918})
            compare("GET", read_req["method"], name)
            compare([read_req["path"]], case["reads"], name + ".reads")
            body = actual["body"]
            results.append(
                {
                    "case": name,
                    "method": actual["method"],
                    "path": actual["path"],
                    "lines": len(body["details"]),
                    "goodsTotalAmount": body["order"]["goodsTotalAmount"],
                }
            )
    return results


def business_values(receipt, document_id):
    """Validate generated identities and relationships before normalizing them."""
    value = deepcopy(receipt)
    header = value["order"]
    compare(document_id, header["id"], "order.id")
    bill = header["billNo"]
    if not bill:
        raise ValueError("Missing ERP bill number")
    seen = {}

    def record(row, table, parent=None):
        key = row["id"]
        if (
            not isinstance(key, int)
            or key <= 0
            or key in seen.setdefault(table, set())
        ):
            raise ValueError(f"Invalid or duplicate {table} primary key")
        seen[table].add(key)
        row["id"] = "<generated>"
        compare(bill, row["billNo"], table + ".billNo")
        row["billNo"] = "<bill>"
        if table != "order":
            compare(document_id, row["orderId"], table + ".orderId")
            row["orderId"] = "<order>"
        if parent is not None:
            compare(parent, row["detailId"], table + ".detailId")
            row["detailId"] = "<parent>"
        for field in ("createdTime", "updatedTime"):
            datetime.fromisoformat(row[field])
            row[field] = "<server time>"
        return key

    record(header, "order")
    for row in value["details"]:
        key = record(row, "details")
        for table in ("batches", "workers", "forklifts"):
            for child in row.get(table, []):
                record(child, table, key)
    for payment in value["payments"]:
        record(payment, "payments")
    return value


async def persisted_cases(reference, base_url, token, invoke):
    results = []
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/") + "/",
        trust_env=False,
        follow_redirects=False,
        headers={"Authorization": f"Bearer {token}"} if token else {},
        timeout=15,
    ) as client:
        for case in reference["cases"]:
            if case["name"] not in {"edited", "piecework", "payment"}:
                continue
            ref = case["request"]
            response = await client.request(
                ref["method"], ref["path"].lstrip("/"), json=ref["body"]
            )
            response.raise_for_status()
            envelope = response.json()
            if envelope.get("code") != 200:
                raise ValueError(
                    f"Original request: {envelope.get('message')}"
                )
            first = envelope["data"]
            print(f"{case['name']} 原客户端保存 ID {first}", flush=True)
            second = await invoke("save", case["input"])
            print(f"{case['name']} 工具保存 ID {second}", flush=True)
            left_response = await client.get(f"purchase/order/{first}")
            left_response.raise_for_status()
            left = left_response.json()["data"]
            right = await invoke("detail", {"id": second})
            if (
                first == second
                or left["order"]["billNo"] == right["order"]["billNo"]
            ):
                raise ValueError("Expected two independent receipts")
            values = business_values(left, first)
            compare(values, business_values(right, second), case["name"])
            digest = hashlib.sha256(
                json.dumps(values, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            results.append(
                {
                    "case": case["name"],
                    "originalBill": left["order"]["billNo"],
                    "toolBill": right["order"]["billNo"],
                    "businessValuesSha256": digest,
                }
            )
    return results


async def persisted_required_cases(bundle, reference, invoke):
    """Replay the live browser cases through the normal tool entry points."""
    from unittest.mock import patch

    save = bundle["tools"]["save"]
    page = bundle["discovery"]["createCall"]["file"]
    compare(reference["pageSha256"], save["plan"]["hashes"][page])
    compare(
        [
            "required-header",
            "required-detail",
            "required-batch",
            "required-complete",
        ],
        [case["name"] for case in reference["cases"]],
    )
    argument = save["plan"]["editorProgram"]["argument"]
    results = []
    send = httpx.AsyncClient.send
    requests = []

    async def record(client, request, *args, **kwargs):
        requests.append(request)
        return await send(client, request, *args, **kwargs)

    for case in reference["cases"]:
        name, expected = case["name"], case["request"]
        requests.clear()
        with patch.object(httpx.AsyncClient, "send", record):
            try:
                receipt_id = await invoke(
                    "save", {argument: case["input"][argument]}
                )
            except ValueError as exc:
                if expected:
                    raise
                compare(case["message"], str(exc), name)
                compare([], case["reads"], name + ".clientReads")
                compare(
                    [],
                    [r.url.path for r in requests if r.method != "GET"],
                    name + ".toolWrites",
                )
                results.append(
                    {
                        "scenario": name,
                        "originalPrompt": case["message"],
                        "toolPrompt": str(exc),
                        "originalSaves": 0,
                        "toolSaves": 0,
                    }
                )
                continue
        if expected is None:
            raise ValueError(f"{name}: tool saved a draft the client rejected")
        writes = [request for request in requests if request.method != "GET"]
        compare(1, len(writes), name + ".writes")
        compare(expected["method"], writes[0].method)
        if not writes[0].url.path.endswith(expected["path"]):
            raise ValueError(f"{name}: tool used a different request path")
        compare(
            expected["body"], json.loads(writes[0].content), name + ".request"
        )
        first = int(case["reads"][0].rsplit("/", 1)[-1])
        original = await invoke("detail", {"id": first})
        saved = await invoke("detail", {"id": receipt_id})
        if (
            first == receipt_id
            or original["order"]["billNo"] == saved["order"]["billNo"]
        ):
            raise ValueError("Expected two independent receipts")
        values = business_values(original, first)
        compare(
            values, business_values(saved, receipt_id), name + ".persisted"
        )
        results.append(
            {
                "scenario": name,
                "completeRequestEqual": True,
                "originalBill": original["order"]["billNo"],
                "toolBill": saved["order"]["billNo"],
                "persistedBusinessFieldsEqual": True,
                "goodsTotalAmount": saved["order"]["goodsTotalAmount"],
                "remark": saved["order"]["remark"],
                "detailRemark": saved["details"][1]["remark1"],
                "batchLocation": saved["details"][1]["batches"][0][
                    "warehouseLocation"
                ],
            }
        )
    return results
