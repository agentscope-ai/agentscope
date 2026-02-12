# Research: 010-formatter-tests-sync

## Decision: tests-only absorption

- **Decision**: Absorb only `19cba5c` in this batch.
- **Rationale**: Lowest conflict risk; improves CI stability without affecting
  runtime behavior or easy-only features.
- **Alternatives considered**:
  - Bundle with formatter runtime refactors (rejected: higher blast radius).
