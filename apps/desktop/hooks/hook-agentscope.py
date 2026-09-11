# -*- coding: utf-8 -*-
"""PyInstaller hook for AgentScope package data and dynamic drivers."""
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

datas = collect_data_files("agentscope")
datas += collect_data_files(
    "agentscope",
    include_py_files=True,
    includes=["app/storage/_sql/_alembic/**"],
)
datas += collect_data_files(
    "agentscope.tool._builtin._scripts",
    include_py_files=True,
)
datas += copy_metadata("qdrant-client")
hiddenimports = [
    "agentscope.tool._builtin._scripts",
    "aiosqlite",
    "portalocker",
    "sqlalchemy.dialects.sqlite.aiosqlite",
]
