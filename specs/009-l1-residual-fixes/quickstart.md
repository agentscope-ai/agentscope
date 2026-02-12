# Quickstart: 009-l1-residual-fixes

## 1. Verify deprecation behavior

- Invoke chat models repeatedly with `tool_choice="any"`.
- Confirm behavior maps to required tool invocation.
- Confirm deprecation warning does not spam repeatedly.

## 2. Verify Qwen-Omni audio formatting

- Build request messages with `input_audio` using base64 data.
- Use omni model name and invoke model path.
- Confirm audio data is prefixed as required before API call.

## 3. Run checks

- `pre-commit run --all-files`
- `./.venv/bin/python -m ruff check src`
- `./.venv/bin/python -m pylint -E src`
- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q tests`
