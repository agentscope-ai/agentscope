# Research: Version and Dependency Alignment (A+B)

## Decision 1: Target Release Version

- **Decision**: Align easy's package version to 1.0.10.
- **Rationale**: Upstream A+B bumps 1.0.9 then 1.0.10; the latest is the
  intended stable tag for this sync.
- **Alternatives considered**:
  - 1.0.9: rejected because 1.0.10 supersedes it.
  - 1.0.5: rejected because it defeats alignment with upstream.

## Decision 2: mem0ai Pin Location

- **Decision**: Apply mem0ai<=0.1.116 only in the existing full extra.
- **Rationale**: Aligns with upstream safety without changing minimal installs.
- **Alternatives considered**:
  - Pin in install_requires: rejected to avoid tightening minimal dependency set.
  - Exact pin: rejected to keep patch-level flexibility.

## Decision 3: OpenTelemetry Baseline

- **Decision**: Enforce OpenTelemetry minimum versions >=1.39.0 and include
  opentelemetry-semantic-conventions>=0.60b0 in install requirements.
- **Rationale**: Upstream aligns on these baselines for stability and API
  compatibility.
- **Alternatives considered**:
  - Keep existing versions: rejected due to upstream compatibility issues.
  - Exact pins: rejected to allow patch-level upgrades.
