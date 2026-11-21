"""Command-line interface for source-backed business tools."""

import subprocess
import sys

import httpx
from jsonschema.exceptions import SchemaError

from ._business import main

if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        TypeError,
        SchemaError,
        KeyError,
        OSError,
        httpx.HTTPError,
        subprocess.TimeoutExpired,
    ) as exc:
        sys.exit(str(exc))
