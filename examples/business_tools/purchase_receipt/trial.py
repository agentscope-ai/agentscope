"""Reproduce the two-line Yuanjing purchase receipt case."""

import argparse
import asyncio
import json
import os
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from parity import check_cases, persisted_cases, persisted_required_cases

from agentscope.business import as_tool, discovery_tool
from agentscope.tool import Toolkit

HERE = Path(__file__).resolve().parent


def read_json(path):
    return json.loads(path.read_bytes())


def compare(label, actual, expected):
    if actual != expected:
        raise ValueError(
            f"{label}: expected {expected!r}, received {actual!r}"
        )


async def run(args):
    with TemporaryDirectory() as scratch:
        output = args.output or Path(scratch) / "workflow.json"
        kit = Toolkit(
            tools=[discovery_tool(args.source, args.openapi, output)]
        )
        discover = await kit.get_tool("generate_business_tools")
        reply = await discover(business="采购入库单")
        print(reply.content[0].text)
        bundle = read_json(output)
        artifacts = bundle["tools"]
    save, detail = artifacts["save"], artifacts["detail"]
    original = read_json(args.input)
    reference = read_json(args.reference)
    results = {"client": check_cases(bundle, reference)}
    for case in results["client"]:
        print(case["case"], case.get("rejected", case.get("goodsTotalAmount")))
    if not any(
        (
            args.compare_persisted,
            args.required_reference,
            args.submit,
            args.read_id is not None,
        )
    ):
        if args.results:
            args.results.write_text(
                json.dumps(results, ensure_ascii=False, indent=2) + "\n"
            )
        return
    base = args.base_url
    if not base:
        raise ValueError("Supply --base-url to use a running ERP")
    token = os.environ.get("BUSINESS_API_TOKEN")
    toolkit = Toolkit(tools=[as_tool(a, base, token) for a in (save, detail)])

    async def invoke(role, values):
        tool = await toolkit.get_tool(artifacts[role]["operation"]["name"])
        text = (await tool(**values)).content[0].text
        try:
            return json.loads(text)["result"]["data"]
        except (KeyError, ValueError) as exc:
            raise ValueError(text) from exc

    if args.required_reference:
        results["live"] = await persisted_required_cases(
            bundle, read_json(args.required_reference), invoke
        )
        print(json.dumps(results["live"], ensure_ascii=False))
        if args.results:
            args.results.write_text(
                json.dumps(results, ensure_ascii=False, indent=2) + "\n"
            )
        return

    if args.compare_persisted:
        results["persisted"] = await persisted_cases(
            reference, base, token, invoke
        )
        print(json.dumps(results["persisted"], ensure_ascii=False))
        if args.results:
            args.results.write_text(
                json.dumps(results, ensure_ascii=False, indent=2) + "\n"
            )
        return

    # Preserve the single-draft trial and read-only verification on another ledger.
    scenario = read_json(HERE / "expected.json")[-1]
    inputs = deepcopy(original)
    inputs["data"]["details"][1].update(scenario["patch"])
    rows = inputs["data"]["details"]
    total = Decimal(str(scenario["total"]))
    doc_id = args.read_id
    if args.submit:
        doc_id = int(await invoke("save", inputs))
        print(
            f"已保存 ID {doc_id}。可用 --read-id {doc_id} 单独回查。",
            flush=True,
        )
    receipt = await invoke("detail", {"id": doc_id})
    header = receipt["order"]
    compare("detail count", len(receipt["details"]), len(rows))
    for saved, sent, predicted in zip(
        receipt["details"], rows, scenario["rows"], strict=True
    ):
        sent = {**sent, **predicted}
        for field in (
            "materialId",
            "expectPcs",
            "inPcs",
            "factoryPricePcs",
            "totalLoadingFee",
            *scenario["rows"][0],
        ):
            compare(
                field, Decimal(str(saved[field])), Decimal(str(sent[field]))
            )
    compare("id", int(header["id"]), doc_id)
    compare(
        "goodsTotalAmount", Decimal(str(header["goodsTotalAmount"])), total
    )
    if not header.get("billNo"):
        raise ValueError("The ERP returned no bill number")
    print(
        json.dumps(
            {
                "id": doc_id,
                "billNo": header["billNo"],
                "status": header["billStatus"],
                "lineAmounts": [
                    r["payableAmount"] for r in receipt["details"]
                ],
                "goodsTotalAmount": header["goodsTotalAmount"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="ERP frontend src directory"
    )
    parser.add_argument(
        "--openapi", type=Path, required=True, help="ERP OpenAPI JSON file"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=HERE / "two_lines.json",
        help="Two-line fixture with the test account's master-data IDs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Keep the generated workflow and its source evidence",
    )
    parser.add_argument(
        "--reference", type=Path, default=HERE / "client_cases.json"
    )
    parser.add_argument(
        "--results", type=Path, help="Save the differential acceptance results"
    )
    parser.add_argument("--base-url", help="ERP API root, including /api")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--required-reference",
        type=Path,
        help="Compare to a live browser capture from --base-url",
    )
    mode.add_argument(
        "--compare-persisted",
        action="store_true",
        help="Create two drafts for each of three paired cases on the test ERP",
    )
    mode.add_argument(
        "--submit",
        action="store_true",
        help="Create one new draft in the selected ERP",
    )
    mode.add_argument(
        "--read-id", type=int, help="Verify an existing trial receipt by ID"
    )
    asyncio.run(run(parser.parse_args()))
