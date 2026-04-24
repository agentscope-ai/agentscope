## GLM (Zhipu AI) Provider Testing Report

| Test Dimension | Model | Integration Test | Mock Test | Notes |
|---|---|---|---|---|
| Non-streaming response | glm-4-flash | PASSED | PASSED | |
| Streaming response | glm-4-flash | PASSED | PASSED | Mock replays real captured `ChatCompletionChunk` sequence (13 chunks) |
| Reasoning model | glm-4.7-flash | PASSED | PASSED | `enable_thinking` passed via `extra_body`, `reasoning_content` correctly parsed into `ThinkingBlock` |
| Multimodal input | glm-4v-flash | PASSED | PASSED | |
| Multimodal output | — | SKIPPED | SKIPPED | GLM does not currently support multimodal output |

**Framework compatibility:** No issues found. `OpenAIChatModel` works out-of-the-box with the GLM provider.

### Key Findings

- **`extra_body` passthrough**: `OpenAIChatModel.__call__` merges all `**kwargs` into the API request dict (line 244 of `_openai_model.py`). The OpenAI Python SDK recognizes `extra_body` and forwards it into the HTTP request body. This allows GLM-specific parameters like `enable_thinking=True` to be passed without any framework modification.

- **`reasoning_content` parsing**: The framework already checks for `choice.message.reasoning_content` (non-streaming, line 558) and `choice.delta.reasoning_content` (streaming, line 386) via `getattr`. GLM's thinking-mode response uses this same attribute name, so `ThinkingBlock` objects are produced automatically.

- **Multimodal input**: GLM's vision model (`glm-4v-flash`) accepts the standard OpenAI `image_url` content block format. The framework passes message content through unchanged, requiring no special handling.

### Test File Locations

| File | Purpose |
|---|---|
| `tests/integration/test_glm_chat_model.py` | Real API integration tests (requires `ZAI_API_KEY`) |
| `tests/integration/fixtures/*.json` | Captured API responses for mock replay (includes raw `ChatCompletionChunk` stream) |
| `tests/unit/test_glm_chat_model_mock.py` | Mock unit tests for CI (no API key needed) |

### Running the Tests

```bash
# Mock tests (CI-safe, no API key required)
python -m pytest tests/unit/test_glm_chat_model_mock.py -v

# Integration tests (requires real API key)
ZAI_API_KEY=your_key python -m pytest tests/integration/test_glm_chat_model.py -v
```
