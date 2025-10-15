Module: `src/agentscope/filesystem/_types.py`
Responsibility: Define logical filesystem primitives shared across the module.
Key Types:
- `Path`: logical absolute path string (must pass `validate_path`).
- `Operation`: literal set of supported actions (`"list"`, `"read_file"`, etc.).
- `Grant`: `TypedDict` describing `{prefix, ops}` authorization tuples.
- `EntryMeta`: metadata returned from snapshots and read/write operations (`path`, `size`, `updated_at`).

Module: `src/agentscope/filesystem/_errors.py`
Responsibility: Exception hierarchy for logical filesystem failures.
Key Classes:
- `FileSystemError(RuntimeError)`: base exception carrying `path` when available.
- `InvalidPathError`, `AccessDeniedError`, `NotFoundError`, `ConflictError`, `InvalidArgumentError`: specialised errors surfaced by handles/backends.

Module: `src/agentscope/filesystem/_base.py`
Responsibility: Abstract backend contract for logical filesystem implementations.
Key Methods:
- `create_handle(grants: Sequence[Grant]) -> FsHandle`
  - Purpose: bind a sequence of grants to a handle instance.
  - Inputs: `Sequence[Grant]` describing prefix → allowed ops.
  - Returns: `FsHandle` referencing `self`.
  - Notes: local import avoids cycle with `_handle.py`.
  - Type Safety: Ensure grants are validated before handle creation.
- `_snapshot_impl(grants: Sequence[Grant]) -> dict[Path, EntryMeta]`
  - Purpose: authoritative metadata view filtered by grants.
  - Returns: `dict[Path, EntryMeta]` keyed by logical path.
  - Type Safety: Use consistent generic syntax, avoid `Dict` imports.
- `_read_binary_impl(path: Path) -> bytes`
- `_read_file_impl(path: Path, *, index: int | None, line: int | None) -> str`
- `_read_re_impl(path: Path, pattern: str, overlap: int | None) -> list[str]`
  - Purpose: backend-specific data retrieval.
  - Notes: `FsHandle` validates args before delegation.
  - Type Safety: All parameters must be properly typed, especially optional int parameters.
- `_write_impl(path: Path, data: bytes | str, overwrite: bool) -> EntryMeta`
- `_delete_impl(path: Path) -> None`
  - Purpose: mutate backend state and return updated metadata.
  - Type Safety: Consistent union types (`bytes | str`) and proper return annotations.

Module: `src/agentscope/filesystem/_handle.py`
Responsibility: Enforce path validation, grant checks, and expose the public API.
Key Methods:
- `validate_path(path: Path) -> Path`
  - Purpose: ensure absolute path semantics (leading `/`, no control chars, no `..`, `*`, `?`, `\\`, `//`).
  - Type Safety: Input type must be `Path` alias, returns validated path.
  - Errors: `InvalidPathError` on violations.
- `FsHandle.__init__(filesystem: FileSystemBase, grants: Sequence[Grant]) -> None`
  - Type Safety: Store grants as `List[Grant]` and index as `Dict[Path, EntryMeta]`.
- `FsHandle.list(prefix: Path | None = None) -> List[EntryMeta]`
  - Purpose: refresh snapshot and return metadata list (optionally filtered by prefix).
  - Preconditions: caller holds `"list"` capability for the prefix (or any prefix if `None`).
  - Returns: `List[EntryMeta]` sorted by path.
  - Type Safety: Use consistent generic syntax, return list copies.
- `FsHandle.read_file(path: Path, *, index: int | None = None, line: int | None = None) -> str`
- `FsHandle.read_re(path: Path, pattern: str, *, overlap: int | None = None) -> List[str]`
- `FsHandle.write(path: Path, data: bytes | str, *, overwrite: bool = True) -> EntryMeta`
- `FsHandle.delete(path: Path) -> None`
  - Shared Flow: validate path → ensure grant covers operation → refresh snapshot → ensure existence/conflict rules → delegate to backend hook → refresh snapshot (write/delete).
  - Type Safety: All optional int parameters must use `_int | None` syntax.
  - Errors: `AccessDeniedError`, `InvalidArgumentError`, `ConflictError`, `NotFoundError` per SOP.

Module: `src/agentscope/filesystem/_memory.py`
Responsibility: Reference in-memory backend demonstrating snapshot + hook contract.
Key Methods:
- `_snapshot_impl(grants)`
  - Purpose: build visible metadata map respecting grant prefixes.
  - Notes: protected by `threading.RLock` for atomicity.
- `_read_binary_impl/_read_file_impl/_read_re_impl`
  - Purpose: operate on UTF-8 text buffer stored in `_store`.
  - Notes: `_read_re_impl` supports optional overlap; `_read_file_impl` slices by line index/count.
- `_write_impl`
  - Purpose: persist bytes, update `EntryMeta` with size and UTC timestamp; respects `overwrite` guard.
- `_delete_impl`
  - Purpose: remove entry, raising `NotFoundError` when absent.

Module: `src/agentscope/filesystem/_builtin.py`
Responsibility: Provide the three-namespace reference configuration atop the logical filesystem.
Key Elements
- Constants `INTERNAL_PREFIX`, `USERINPUT_PREFIX`, `WORKSPACE_PREFIX`.
- Helper functions `builtin_grant`/`builtin_grants` (copy default grant definitions for composition).
- `BuiltinFileSystem` convenience methods `create_internal_handle`, `create_userinput_handle`, `create_workspace_handle`, `create_handle_for`.
Notes
- Internal namespace lacks `delete` privilege to prevent log removal.
- Userinput namespace is read-only; workspace exposes full read/write/delete cycle.

Call Graph (core operations)
- Caller → `FsHandle.write` → `validate_path` → `_ensure_allowed` → `_refresh_index` (`FileSystemBase._snapshot_impl`) → `_write_impl` → `_refresh_index` → Caller.
- Caller → `FsHandle.read_file` → `validate_path` → `_ensure_allowed` → `_refresh_index` → `_ensure_exists` → `_read_file_impl` → Caller.

Type Safety Standards
- **Generic Type Syntax**: Prefer built-in generics (`dict[Path, EntryMeta]`) over legacy `Dict`
- **Union Types**: Use modern syntax (`bytes | str`) rather than `Union`
- **Optional Parameters**: Annotate explicitly (`index: int | None = None`)
- **Return Annotations**: Every public method must declare its return type
- **Imports**: Keep `typing` imports minimal; rely on built-in containers where possible
- **TYPE_CHECKING Patterns**: Document why conditional imports are needed
- **TypedDict Usage**: Ensure optional vs required fields are explicit (`total=False` helpers)

Example Integration
- Agent/Tool obtains handle via `FileSystemBase.create_handle(grants)`.
- Handle mediates every operation; backends never receive unvalidated paths.
- Memory backend enables unit tests and serves as template for durable implementations.

Related SOP: `docs/filesystem/SOP.md`
