# Implementation Plan: Absorb main low-risk pointfixes (L1)

**Branch**: `004-main-pointfixes` | **Date**: 2026-02-08 | **Spec**: specs/004-main-pointfixes/spec.md
**Input**: Feature specification from `/specs/004-main-pointfixes/spec.md`

## Summary

Absorb a small bundle of upstream L1 pointfixes from `main` into `easy` to
improve robustness and platform compatibility, while preserving all easy-only
features (SubAgent/Filesystem/Search/Web + SOP/spec governance).

## Technical Context

**Language/Version**: Python 3.10+ (repo supports 3.10+)  
**Primary Dependencies**: Packaging via `setup.py`; runtime deps via pip  
**Testing**: pytest + pre-commit + ruff + pylint  
**Target Platform**: Ubuntu CI + Windows + macOS matrices  
**Project Type**: Single package (`src/agentscope`, `tests/`)

## Scope

Upstream commits targeted (from local `main`):
- `3b67178` OpenAI streaming: handle `choice.delta` possibly being None
- `44b6806` DeepSeek: support `reasoning_content`
- `267cea0` DashScope embedding: correct dimension argument
- `8299e1b` Windows: ensure utf-8 env for `execute_python_code`
- `6bc219a` JSON repair: ensure `_json_loads_with_repair` returns dict for
  partial JSON strings

Files expected to change:
- `src/agentscope/model/_openai_model.py`
- `src/agentscope/formatter/_deepseek_formatter.py`
- `src/agentscope/embedding/_dashscope_embedding.py`
- `src/agentscope/tool/_coding/_python.py`
- `src/agentscope/_utils/_common.py`

## Constraints

- No public contract/schema changes.
- No packaging migration to `pyproject.toml`.
- Minimal diff; prefer cherry-pick where it applies cleanly, otherwise manual
  port with equivalent behavior.

## Verification

- `pre-commit run --all-files`
- `./.venv/bin/python -m ruff check src`
- `./.venv/bin/python -m pylint -E src`
- `./.venv/bin/python -m pytest -q`
