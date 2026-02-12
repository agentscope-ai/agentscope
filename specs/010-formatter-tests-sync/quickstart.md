# Quickstart: 010-formatter-tests-sync

## Run checks

- `pre-commit run --all-files`
- `./.venv/bin/python -m ruff check src`
- `./.venv/bin/python -m pylint -E src`

## Run focused tests

- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q \
  tests/formatter_anthropic_test.py \
  tests/formatter_deepseek_test.py \
  tests/formatter_gemini_test.py \
  tests/formatter_ollama_test.py`
