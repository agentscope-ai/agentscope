# Quickstart Verification

1. Search for stale naming:
   - `rg -n "client_args" src tests docs examples`
2. Run lints:
   - `./.venv/bin/python -m ruff check src tests docs examples`
   - `./.venv/bin/python -m pylint -E src`
3. Run focused tests:
   - `./.venv/bin/python -m pytest tests/model_openai_test.py tests/model_gemini_test.py tests/model_anthropic_test.py`
