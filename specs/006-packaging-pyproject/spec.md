# Feature Specification: Align packaging to pyproject.toml (keep full/dev extras)

**Feature Branch**: `006-packaging-pyproject`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-02-08
**Status**: Draft
**Input**: User description: "尽可能简单，尽量跟 main 分支习惯一致；不丢失 easy 独家内容；明确 minimal vs extras 边界。"

## Clarifications

- Scope is packaging/metadata only: no runtime behavior changes, no public API
  changes, no tool schema changes.
- Goal is to adopt `main`-style packaging layout (`pyproject.toml` + PEP 621)
  while preserving `easy`-specific constraints (e.g., optional-dep import
  safety, Windows markers).
- Keep extras keys minimal and stable: only `full` and `dev` (no new extras).
- Phase 1 only: do not attempt to slim `dependencies` aggressively in this
  feature; keep behavior compatible and avoid surprising users. A separate
  feature can later move more provider SDKs into extras if desired.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Install/build works with pyproject (Priority: P1)

As a maintainer, I want the project to build and install correctly using a
`pyproject.toml`-based configuration, matching upstream `main` habits, so the
release pipeline and editable installs remain stable.

**Why this priority**: Packaging breakage is a hard failure for all users and
blocks releases/CI.

**Independent Test**:

- `python -m build` succeeds.
- Installing the wheel and importing `agentscope` works.

**Acceptance Scenarios**:

1. **Given** a clean environment, **When** I run `python -m build`, **Then** a
   wheel/sdist is produced successfully.
2. **Given** the built wheel, **When** I `pip install dist/*.whl` and run
   `python -c "import agentscope; print(agentscope.__version__)"`, **Then** it
   prints the expected version.
3. **Given** a source checkout, **When** I run `pip install -e '.[dev]'`,
   **Then** installation succeeds without requiring manual dependency steps.

---

### User Story 2 - Extras boundary is explicit and stable (Priority: P1)

As a user, I want to install a minimal runtime by default and opt into heavier
capabilities via extras, so I don't get dragged into a full dependency stack
unless I ask for it.

**Why this priority**: Prevents default installs from becoming unreasonably
heavy and keeps optional integrations isolated.

**Independent Test**:

- Extras remain exactly `full` and `dev`.
- `dev` remains a superset of `full`.

**Acceptance Scenarios**:

1. **Given** `pyproject.toml`, **When** I inspect optional-dependencies,
   **Then** only `full` and `dev` exist (no new extras keys).
2. **Given** `pip install -e '.[full]'` and `pip install -e '.[dev]'`,
   **When** I compare their intended dependency sets, **Then** `dev` includes
   all `full` dependencies plus development tools.

---

### User Story 3 - Easy-only features remain optional-dep safe (Priority: P1)

As a maintainer, I want `easy`-specific features (SubAgent/Filesystem/Search/RAG
Tier-A additions) to remain intact and optional-dependency safe after the
packaging migration.

**Why this priority**: Packaging refactors must not regress the easy ecosystem.

**Independent Test**:

- `import agentscope` and `import agentscope.rag` remain import-safe.
- Windows marker for Milvus Lite extras remains in place.

**Acceptance Scenarios**:

1. **Given** missing optional deps (`python-docx`, `pymilvus`), **When** I
   `import agentscope.rag`, **Then** import succeeds; errors occur only when
   actually using the optional feature.
2. **Given** Windows, **When** I install `.[dev]`, **Then** the Milvus Lite
   dependency is excluded via markers and installation remains green.

### Edge Cases

- Editable install with extras must not self-reference and cause recursive
  resolution.
- `py.typed` must remain included in built artifacts.
- Dependency resolution conflicts (e.g., qdrant-client vs pymilvus) should be
  minimized; follow upstream pins where appropriate.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST add `pyproject.toml` using PEP 621 metadata and
  `setuptools.build_meta` backend (align with upstream `main` habit).
- **FR-002**: System MUST keep distribution name `agentscope` and dynamic
  version sourced from `agentscope._version.__version__`.
- **FR-003**: System MUST keep extras keys exactly: `full`, `dev`.
- **FR-004**: System MUST keep `dev` as a superset of `full` (dev tools +
  optional capability deps).
- **FR-005**: System MUST ensure `py.typed` is packaged in wheels/sdists.
- **FR-006**: System MUST preserve easy-only optional dependency constraints,
  including Windows markers for Milvus Lite.
- **FR-007**: System MUST NOT change runtime public APIs or tool schemas.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `python -m build` succeeds.
- **SC-002**: `pip install dist/*.whl` + `import agentscope` succeeds.
- **SC-003**: `pip install -e '.[dev]'` succeeds in CI.
- **SC-004**: CI quality gates remain green (pre-commit / ruff / pylint / pytest).

