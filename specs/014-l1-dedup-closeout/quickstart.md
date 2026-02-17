# Quickstart: Verify no-op absorption closure for 014

## 1) Confirm target commits are not direct ancestors of easy

```bash
git merge-base --is-ancestor 62aa639 easy; echo $?
git merge-base --is-ancestor 6bc219a easy; echo $?
```

Expected: non-zero (not direct ancestors).

## 2) Confirm equivalent behavior exists on easy

```bash
git log --oneline -- pyproject.toml src/agentscope/py.typed | head
git log --oneline -- src/agentscope/_utils/_common.py | head
```

Expected:
- packaging/py.typed support present in current files.
- `_json_loads_with_repair` dict-safe implementation present.

## 3) Confirm this branch is specs-only

```bash
git diff --name-only easy...HEAD
```

Expected: only `specs/014-l1-dedup-closeout/*`.

## 4) Run quality gate

```bash
pre-commit run --all-files
```
