# -*- coding: utf-8 -*-
"""Start an ACP agent from an application-owned session factory."""

import argparse
import asyncio
import importlib
import sys
from contextlib import redirect_stdout

from ._stdio import serve_acp


def main() -> None:
    """Load a factory without allowing import-time prints onto stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--factory",
        required=True,
        help="Importable module:function returning an async Agent context",
    )
    args = parser.parse_args()
    module_name, separator, name = args.factory.partition(":")
    if not separator or not module_name or not name:
        parser.error("--factory must be module:function")
    with redirect_stdout(sys.stderr):
        factory = getattr(importlib.import_module(module_name), name)
    if not callable(factory):
        parser.error("The specified factory is not callable")
    asyncio.run(serve_acp(factory))


if __name__ == "__main__":
    main()
