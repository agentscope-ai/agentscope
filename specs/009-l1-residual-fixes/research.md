# Research: 009-l1-residual-fixes

## Decision 1: Keep deprecated `tool_choice="any"` compatible with warning

- **Decision**: Translate `"any"` to `"required"` while warning once per process.
- **Rationale**: Preserves existing callers, reduces warning noise, and avoids
  hard-breaking behavior in easy.
- **Alternatives considered**:
  - Remove `"any"` immediately (rejected: high compatibility risk).
  - Keep unrestricted warnings (rejected: log spam and poor operability).

## Decision 2: Apply provider-specific audio normalization for omni models only

- **Decision**: Inject base64 prefix for omni models before request dispatch.
- **Rationale**: Fixes provider compatibility without changing non-omni models.
- **Alternatives considered**:
  - Apply to all models (rejected: unnecessary behavior change).
  - Require user-side formatting (rejected: breaks current abstraction).

## Decision 3: Defer large formatter extraction/promoting refactors

- **Decision**: Exclude `bd5d926` and `f5fdc37` from this batch.
- **Rationale**: Multi-file high-diff changes are above current L1 scope and
  raise merge/regression risk.
- **Alternatives considered**:
  - Include in same batch (rejected: violates minimal-diff strategy).
