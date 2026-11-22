# -*- coding: utf-8 -*-
"""Browse the bundled mock reimbursement application entirely offline."""
import argparse
from pathlib import Path

from agentscope.business.source_catalog import SourceCatalog


def main() -> None:
    """Show the capability list or explain actions mentioned by name."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default="")
    args = parser.parse_args()
    catalog = SourceCatalog(
        "expenses",
        Path(__file__).parent / "reimbursement",
        ("Expenses.vue", "requests.ts", "routes.py"),
        lambda reader: reader == "demo-reader",
    )
    print(catalog.offline_answer("demo-reader", args.question))


if __name__ == "__main__":
    main()
