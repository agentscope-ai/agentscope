# Quickstart: Validate 013 plan+wordreader absorption

## 1) Lint and hooks

```bash
pre-commit run --all-files
./.venv/bin/python -m ruff check src
./.venv/bin/python -m pylint -E src
```

## 2) Focused regression tests

```bash
./.venv/bin/python -m pytest tests/plan_test.py
```

## 3) Optional broader verification

```bash
./.venv/bin/python -m pytest tests/rag_reader_test.py -k word -q
```

## Expected outcomes

- Plan notebook state export/import includes required plan and subtask fields.
- Subtask state updates refresh plan state message/behavior.
- Word reader executes without runtime import/type regressions.
