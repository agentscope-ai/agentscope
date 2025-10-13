# 参考实现补充：Toolkit 组装（闭包唯一路线）

本文档为 `docs/filesystem/SOP.md` 的补充说明：仓库已在
`src/agentscope/filesystem/_toolkit_helper.py` 提供 `create_filesystem_toolkit()`，
按 SOP 的“闭包捕获句柄（MUST）”封装了 `BuiltinFileSystem × Toolkit` 的工具组装，
直接返回装配好的 `BuiltinFileSystem` 与 `Toolkit`（包含 ws_list/ws_file/ws_read_file/
ws_read_re/ws_write/ws_delete 六个工具），工具签名仅暴露 JSON 参数，Schema 中不含 handle。

