---

description: "Close out already-absorbed L1 commits (62aa639 + 6bc219a)"

---

# Tasks: Close out already-absorbed L1 commits

**Input**: `specs/014-l1-dedup-closeout/spec.md`, `specs/014-l1-dedup-closeout/plan.md`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Branch**: `014-l1-dedup-closeout`

## Constitution Gates (applies to all tasks)

- If equivalence fails, stop and open a real absorption branch.
- Keep this branch specs-only if equivalence succeeds.

## Phase 1: Discovery

- [x] T001 Confirm target commits `62aa639` and `6bc219a` are not direct ancestors of easy
- [x] T002 Collect target commit file lists and patch intent
- [x] T003 Collect easy-side equivalent commit evidence (`932e108`, `7645c47`)

## Phase 2: Documentation

- [x] T004 [US1] Write evidence mapping in `research.md`
- [x] T005 [US2] Produce full speckit docs in `specs/014-l1-dedup-closeout/*`
- [x] T006 [US2] Mark no source-code changes in contracts/data-model docs

## Phase 3: Verification

- [x] T007 Verify branch diff is specs-only
- [x] T008 Run `pre-commit run --all-files`
- [x] T009 Commit docs-only closure batch
