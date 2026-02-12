# Research: Absorb main ReActAgent L1 fixes

## Decision 1: Keep scope to three ReActAgent commits

- **Decision**: Absorb only `dd05db2`, `df96805`, `d3c0c1d`.
- **Rationale**: All three are single-file, low-risk stability fixes with high
  signal and minimal integration risk.
- **Alternatives considered**:
  - Absorb broader ReAct refactors: rejected due to higher conflict risk.

## Decision 2: Preserve easy-only ecosystem boundaries

- **Decision**: Do not touch SubAgent/Filesystem/Search/Web contracts in this
  feature.
- **Rationale**: The fixes are internal to ReActAgent behavior and should not
  alter easy governance or architecture.
- **Alternatives considered**:
  - Opportunistic cleanup in adjacent modules: rejected to keep rollback simple.
