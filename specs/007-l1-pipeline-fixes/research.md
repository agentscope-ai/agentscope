# Research: Absorb main L1 pipeline fixes

## Decision 1: Limit scope to L1 point fixes

- **Decision**: Only absorb small, low-risk upstream fixes that touch a minimal
  set of files and do not introduce new modules or refactors.
- **Rationale**: Preserves easy-only ecosystems and keeps rollback simple.
- **Alternatives considered**:
  - Absorb larger refactors (L3): rejected due to conflict risk and maintenance
    cost.

## Decision 2: Pipeline exception propagation

- **Decision**: After streaming ends, re-raise any exception from the executed
  coroutine task.
- **Rationale**: Prevents silent failures where partial output appears as
  success.
- **Alternatives considered**:
  - Swallow exceptions: rejected as it hides failures.

## Decision 3: Environment-dependent tests should skip when prerequisites missing

- **Decision**: Keep environment-dependent E2E tests as skip when prerequisites
  are missing (already established in easy).
- **Rationale**: Ensures `pytest -q tests` is reproducible in CI and local runs.
