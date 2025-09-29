# -*- coding: utf-8 -*-
"""The text file tool module in agentscope."""
from ._view_text_file import view_text_file
from ._write_text_file import (
    insert_text_file,
    write_text_file,
)

__all__ = [
    "insert_text_file",
    "write_text_file",
    "view_text_file",
]
