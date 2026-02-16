# Data Model: Absorb plan notebook and word reader bugfixes (58a4858)

## Entity: Plan

- **Fields**:
  - `id`: unique plan identifier
  - `name`, `description`, `expected_outcome`
  - `subtasks`: ordered list of `SubTask`
  - `created_at`, `finished_at`
  - `state`: `todo | in_progress | done | abandoned`
  - `outcome`
- **Validation rules**:
  - Terminal states (`done`, `abandoned`) should not be auto-downgraded by
    subtask refresh.
- **State transitions**:
  - `todo -> in_progress` when any subtask enters `in_progress`
  - `in_progress -> todo` when no subtask remains `in_progress`
  - completion/finalization handled explicitly by finish APIs

## Entity: SubTask

- **Fields**:
  - `name`, `description`, `expected_outcome`
  - `state`
  - `created_at`, `finished_at`
  - `outcome`
- **Validation rules**:
  - state must use supported vocabulary, including `abandoned`.

## Entity: InMemoryPlanStorage

- **Fields**:
  - `plans`: ordered mapping `plan_id -> Plan`
- **Serialization**:
  - Export as JSON-compatible dictionary via model dump
  - Recover to ordered plan objects via model validation

## Entity: Word Reader Extraction Unit

- **Fields/Types**:
  - paragraph/table/image extraction helpers with runtime-safe imports
- **Validation rules**:
  - Type-checking references should not force runtime-only imports.
