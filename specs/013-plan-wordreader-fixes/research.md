# Research: Absorb plan notebook and word reader bugfixes (58a4858)

## Decision 1: Absorb commit as a single scoped batch

- **Decision**: Cherry-pick `58a4858` into a dedicated feature branch.
- **Rationale**: The commit already groups related plan-state and word-reader
  fixes and includes matching plan test updates.
- **Alternatives considered**:
  - Split by file and reimplement manually (higher drift risk).
  - Bundle with formatter/media batch (higher conflict and regression risk).

## Decision 2: Keep changes within internal behavior boundaries

- **Decision**: Do not expand formatter/media contracts in this branch.
- **Rationale**: Current objective is an L1/L2 scoped fix that preserves
  easy-only characteristics.
- **Alternatives considered**:
  - Absorb adjacent formatter commits in same branch (too broad).

## Decision 3: Treat CI as final oracle if local pytest crashes

- **Decision**: Run local checks first; if local pytest reproduces known crash,
  document blocker and rely on CI result.
- **Rationale**: Prior batches observed intermittent local `pytest RC:139` with
  empty output.
- **Alternatives considered**:
  - Skip pytest entirely (reduces confidence).
