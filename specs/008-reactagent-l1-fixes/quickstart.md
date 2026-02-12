# Quickstart: Absorb main ReActAgent L1 fixes

1. Cherry-pick the 3 target commits from local `main`.
2. Resolve any conflict only in `src/agentscope/agent/_react_agent.py`.
3. Run quality gates:
   - `pre-commit run --all-files`
   - `./.venv/bin/python -m ruff check src`
   - `./.venv/bin/python -m pylint -E src`
   - `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q tests`
