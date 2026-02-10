# Quickstart: Absorb main L1 pipeline fixes

1. Apply upstream point fixes by cherry-picking the targeted commits.
2. Run repository quality gates:
   - `pre-commit run --all-files`
   - `./.venv/bin/python -m ruff check src`
   - `./.venv/bin/python -m pylint -E src`
   - `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q tests`
