# MiniMax TTS PR #2298 review fixes

## Scope and checklist

The user requested fetching and fixing PR #2298 after reviewing its latest
head, `6c040c682450c31aec199a912e30122647147a6a`. Work is isolated from the
existing checkout on `fix/pr-2298-minimax-tts`.

- [x] Fetch the PR and current main.
- [x] Resolve credential/export conflicts with main while retaining MiniMax
  chat model discovery and existing saved credential URLs.
- [x] Add regression tests reproducing incomplete streams, ignored completion,
  JSON error responses, and HTTP client resource leaks.
- [x] Fix stream parsing and completion; preserve incremental audio semantics
  and protection against duplicated terminal aggregate audio.
- [x] Use request-scoped HTTP clients, released on success, failure, generator
  closure, and cancellation.
- [x] Add English parameter attribute docstrings and retain descriptions.
- [x] Run MiniMax and related TTS/chat/application regression tests plus
  repository formatting and static checks in the agentscope conda environment.
- [x] Record results and prepare the local branch for review.

## Decisions

- Keep the existing chat-capable `MiniMaxCredential`; remove the PR's obsolete
  TTS-only override. Strip a trailing `/anthropic` only when building the speech
  URL. The endpoint remains credential-owned, never a model parameter.
- Keep the PR's model catalog, MP3 output and exposed controls. Voice cloning
  and unrelated UI changes are outside this review fix.
- Completion is determined by `data.status == 2`, even without audio. EOF or
  `[DONE]` before that status is an error. Do not silently skip malformed data.
- Parse HTTP JSON responses as whole documents so provider errors propagate.
- Preserve the existing terminal aggregate compatibility behavior, with tests
  for both aggregated audio and completion without audio.

## Validation

- Added transport regressions first: the original implementation failed the
  terminal-status test and 14 subtest cases covering missing completion,
  malformed responses, provider errors, and unclosed clients.
- After fixes, MiniMax-specific tests: **11 passed, 18 subtests passed**.
- Broader regression suite: **195 passed, 438 subtests passed**. Two existing
  dependency deprecation warnings remain (Starlette and Google GenAI).
- Checked AST, YAML, Black, trailing commas, the 79-column limit, Flake8, mypy,
  and Pylint using repository hook settings. All passed, as did
  `git diff --check`.
- Tests run with the `agentscope` conda Python and `PYTHONPATH=src` to import
  this isolated worktree. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` avoids an unrelated
  Phoenix/FastMCP plugin import failure in the shared environment.
- HTTPX MockTransport supplies all provider responses; no paid cloud synthesis
  or browser listening test was performed. Frontend sources are unchanged.

Regression command:

```sh
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /Users/qbc/miniconda3/envs/agentscope/bin/python -m pytest \
  tests/tts_minimax_test.py tests/model_minimax_test.py \
  tests/voice_profile_test.py tests/tts_middleware_test.py \
  tests/tts_openai_test.py tests/tts_gemini_test.py \
  tests/tts_dashscope_test.py tests/tts_voice_cloning_client_test.py -q
```
