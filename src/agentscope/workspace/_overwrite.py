# -*- coding: utf-8 -*-
"""Shared in-place editor write for archive-based container backends."""


class WriteUnconfirmedError(RuntimeError):
    """The remote write may have completed without an acknowledgement."""


def overwrite_command(path: str, size: int) -> list[str]:
    """Read the complete draft from stdin before touching the file."""
    script = (
        "import sys\n"
        "size = int(sys.argv[2])\n"
        "data = sys.stdin.buffer.read(size)\n"
        "if len(data) != size: raise EOFError('Incomplete draft')\n"
        "with open(sys.argv[1], 'r+b') as f:\n"
        "    f.write(data)\n"
        "    f.truncate()\n"
    )
    return ["python3", "-c", script, path, str(size)]
