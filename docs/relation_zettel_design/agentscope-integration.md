# Relation Factory AgentScope Integration Design

<!-- Navigation Metadata -->
<!-- Document: design | Audience: ai, backend | Status: v1.0 -->

## Document Information
- **Feature Name**: Relation Factory Agent Stack
- **Version**: 1.0
- **Date**: 2025-09-16
- **Author**: [TBD]
- **Reviewers**: [AI Architect], [Lead Backend], [QA Lead]
- **Related Documents**: docs/relation_zettel_design/system-architecture.md, docs/relation_zettel_design/ui-ux.md, Agent.md, spec-process-guide/process/design-phase.md

## Overview
This document prescribes how the Relation Factory is implemented using AgentScope. It defines agent roles, toolkits, prompts, structured outputs, state persistence, cost governance, and testing to ensure deterministic, auditable behavior. All guidance follows the AgentScope coding rule (Agent.md) and the ReActAgent-first principle.

### Design Goals
- Compose the Relation Factory as a modular AgentScope ReActAgent with well-defined tools.
- Guarantee deterministic, replayable runs through structured outputs, artifact logging, and session persistence.
- Enforce budget-aware execution with graceful degradation and retry governance.
- Minimize hallucination risk by grounding outputs on evidence hashes and strict validators.
- Enable future expansion to planner/worker hierarchies without rearchitecting existing tools.

### Key Decisions
- **ReActAgent baseline**: The Factory uses `ReActAgent` with explicit tool registration to leverage think-act loops while preserving control.
- **Structured results**: All Fuse and Judge outputs use Pydantic schemas fed through `structured_model`; no free-form parsing.
- **State snapshots**: Agent state persisted via `JSONSession` (MVP) or `SqliteSession` for long runs; artifacts stored under `/tmp/factory/{run_id}`.
- **Budget controller**: Each run receives a cost ceiling; surpassing it triggers degrade mode (heuristic recall + deterministic filters).
- **Blacklist integration**: Agent checks `rel_rejects` using toolkit before generating to avoid previously rejected duplicates.

## Agent Architecture
```mermaid
flowchart LR
    subgraph AgentScope
        Planner[RelationFactoryAgent (ReActAgent)]
        Memories[InMemoryMemory]
        SessionMgr[JSONSession]
        Toolkit
    end
    Toolkit --> RecallTool[tool: recall_candidates]
    Toolkit --> ProbeTool[tool: generate_probe]
    Toolkit --> RetrieveTool[tool: tri_retrieve]
    Toolkit --> FuseTool[tool: fuse_claim]
    Toolkit --> JudgeTool[tool: judge_relation]
    Toolkit --> ScoreTool[tool: score_relation]
    Toolkit --> PersistTool[tool: persist_relation]
    Planner --> Memories
    Planner --> Toolkit
    Planner --> SessionMgr
    Toolkit --> BudgetGuard[Budget Controller]
    Planner --> AuditSink[Audit Logger]
```

## Components

### RelationFactoryAgent (ReActAgent)
- **Identity**: investigative assistant proposing Relation-Zettel drafts.
- **Mission**: run Recall→Probe→Tri-Retrieve→Fuse→Judge pipeline under constraints.
- **Constraints**: must respect budget, evidence grounding, temporal validity checks, and structured output contracts.
- **Initialization**: requires `model`, `formatter`, `toolkit`, `memory`, optional `session`. System prompt loaded from versioned markdown (e.g., `prompts/factory.md`).

### Toolkit Functions
1. `recall_candidates(subject_id, object_id, predicate, as_of, budget_state)`
   - Queries vector index (K1=50), FTS (K2=100), entity bridge (IDF weighting) with MMR λ=0.3; returns deduplicated candidate list with initial scores.
2. `generate_probe(subject_summary, object_summary, predicate)`
   - Uses templated question bank; enforces ≤18 Chinese characters and shared entity inclusion; returns array of sub-questions.
3. `tri_retrieve(candidate, probes, strong_terms)`
   - Retrieves evidence spans from notes, summaries, and event/time anchors; ensures strong cue words; clips to 280 characters.
4. `fuse_claim(evidence_bundle)`
   - Calls LLM with structured output schema to produce claim, reason, evidence references; runs quote hash verification.
5. `judge_relation(fused_claim, scoring_inputs)`
   - Applies gating rules (score ≥0.62, novelty ≥0.2, evidence≥2 distinct notes, temporal overlap) and returns accept/reject with reasons.
6. `score_relation(candidate_features)`
   - Computes component scores: `bm25`, `cos`, `npmi`, `time_fresh`, `path2`, `novelty`, final `score`.
7. `persist_relation(run_context, relation_payload)`
   - Upserts into DB (`relations`, `rel_evidence`, `audit_runs`, `rel_audit`), writes artifacts, updates budget usage.
8. `check_blacklist(uniq_key)`
   - Consults `rel_rejects`; aborts if match found.

### Memory and Session
- **Short-term**: `InMemoryMemory(capacity=10)` to store recent thoughts/actions.
- **Long-term**: optional `Mem0LongTermMemory` for multi-session continuity.
- **Sessions**: `JSONSession` capturing agent state per `run_id`; `load_state_dict` enables replay via `replay_run.py`.

### Budget Controller
- Maintains tokens/cost spent; uses aggregator hooking into toolkit calls.
- Exposes `BudgetState` object passed to tools; degrade triggered when `spent >= ceiling`.
- In degrade mode, agent bypasses LLM for Probe/Fuse (uses deterministic heuristics) and tags output `degraded=true`.

## Prompts and Structured Outputs
- **System Prompt Modules**
  - Identity & mission.
  - Process outline (Recall→Probe→Tri-Retrieve→Fuse→Judge).
  - Constraints (budget, temporal validity, evidence counts).
  - Output format instructions referencing structured schemas.
- **Versioning**
  - Prompts stored under `prompts/factory/v1/` with filenames hashed (e.g., `factory_core.md`).
  - `prompt_hash` recorded in `audit_runs` for reproducibility.

### Pydantic Schemas
```python
class EvidenceItem(BaseModel):
    note: str
    span: str
    quote: str

class FuseOutput(BaseModel):
    claim: constr(strip_whitespace=True, min_length=5, max_length=240)
    reason: constr(strip_whitespace=True, min_length=5, max_length=240)
    evidence: conlist(EvidenceItem, min_items=2, max_items=4)

class JudgeOutput(BaseModel):
    decision: Literal["accept", "reject"]
    missing: List[str]
    score: condecimal(gt=0, lt=1)
    novelty: condecimal(gt=0, lt=1)
    temporal_penalty: condecimal(ge=0, le=0.1)
```
Structured outputs stored in message metadata and persisted in DB.

## Interaction Protocol
- Each tool call logged with request/response payload, cost estimate, and elapsed time.
- Messages labeled with `role`, `name`, `content`, `metadata` (`structured`, `cost`, `tool_call`).
- Failures return typed errors (e.g., `ToolError.MISSING_EVIDENCE`); agent must handle via retry or degrade.
- Loop guard: max 8 think/act cycles per candidate; ensures deterministic upper bound.

## State Persistence & Replay
- After each tool call, `session.save(run_id, agent.state_dict())` invoked.
- `/tmp/factory/{run_id}/step_{n}.json` contains tool inputs/outputs, prompt, response, budget snapshot.
- `scripts/replay_run.py --run-id` rehydrates agent state and replays steps for debugging; optionally re-executes to verify determinism.

## Cost Governance
- Per-request `budget_cents` default `≤ 100` (configurable); aggregator tracks token usage via provider APIs.
- If estimated cost of next LLM call exceeds remaining budget, degrade mode triggered.
- Exponential backoff queue stored in `jobs` table: `{kind, payload, attempt, next_at}`; attempts capped at 3.
- Metrics recorded: `relation_factory_cost_cents`, `degraded_runs_total`, `llm_calls_count`.

## Error Handling & Risk Mitigation
- **Evidence mismatch**: If `quote_sha` mismatch, agent rejects candidate and logs to `rel_rejects` with reason `hash_mismatch`.
- **Temporal conflict**: `judge_relation` subtracts 0.1 when `valid_range` of subject/object do not overlap; rejects if <0.62 after penalty.
- **Tool timeouts**: Each tool call has 5s timeout; on timeout, degrade or provide fallback.
- **LLM hallucination**: Strict schema validation + phrase copying rule enforced; failure increments `hallucination_counter` metric.

## Testing Strategy
- **Unit Tests**: For each toolkit function (mock DB/LLM). Validate scoring math, uniq_key normalization.
- **Prompt Tests**: Snapshot tests verifying prompts contain required sections; ensure hashed prompt matches expected.
- **Structured Output Tests**: Feed sample LLM responses, ensure Pydantic validation passes/fails appropriately.
- **Integration**: Simulate full run with test fixtures (notes, relations). Use deterministic stub LLM returning canned outputs.
- **Load**: Benchmark concurrency across 5 simultaneous agent runs; ensure session persistence scales.
- **Replay**: For every production incident, run `replay_run.py` to reproduce state; ensure artifacts suffice.

## Deployment Considerations
- Model provider credentials stored in secrets manager; never checked into repo.
- AgentScope version pinned; check compatibility with Python 3.11.
- Observability: instrument AgentScope with OpenTelemetry spans per tool call.
- Rolling updates: warm new agent workers before draining old ones to avoid lost sessions.

## Checklist
- [ ] Toolkit functions and contracts defined and documented.
- [ ] Prompts versioned, hashed, and stored with metadata.
- [ ] Structured output schemas implemented and wired into `reply` calls.
- [ ] Session persistence strategy operational and tested.
- [ ] Budget/degrade logic implemented with metrics.
- [ ] Error handling covers evidence mismatch, temporal checks, tool failures.
- [ ] AgentScope upgrade path documented.
- [ ] Replay tooling validated.

---
Next: align with docs/relation_zettel_design/ui-ux.md for reviewer experience.

