# Quickstart: 011-mem0-graphstore-fix

## Run checks

- `pre-commit run --all-files`
- `./.venv/bin/python -m ruff check src`
- `./.venv/bin/python -m pylint -E src`

## Focused runtime checks

- Verify relations formatting helper produces:
  - `"Alice -- likes -- Bob"` style lines
- Verify mem0 LLM adapter returns dict with `content` and `tool_calls` when tools are provided
