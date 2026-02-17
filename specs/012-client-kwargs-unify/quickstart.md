# Quickstart: 012-client-kwargs-unify

## Run checks

- `pre-commit run --all-files`
- `./.venv/bin/python -m ruff check src`
- `./.venv/bin/python -m pylint -E src`

## Focused runtime checks

- `DashScopeChatModel.__init__` accepts an unexpected kwarg (should not raise)
- `OllamaChatModel` merges `generate_kwargs` into call kwargs and allows
  `client_kwargs` for client init
