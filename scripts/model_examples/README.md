# Model Call Examples

This directory contains example scripts for the major LLM providers supported by AgentScope, together with a unified test runner `run_tests.py`.
These scripts are designed to verify that AgentScope's chat model components function correctly across various input scenarios.

---

## Directory Layout

```
scripts/model_examples/
├── run_tests.py                          # Unified test runner
├── _utils.py                             # Shared helpers (stream_and_collect)
├── test.jpeg                             # Sample image for multimodal tests
│
├── openai_chat_call.py             # OpenAI Chat Completions – basic + tool call
├── openai_chat_multiagent.py       # OpenAI Chat Completions – multi-agent conversation
├── openai_chat_multimodal.py       # OpenAI Chat Completions – image/text multimodal
├── openai_chat_multiagent_multimodal.py
│
├── openai_response_call.py         # OpenAI Responses API – reasoning models (o1/o3)
├── openai_response_multiagent.py
├── openai_response_multimodal.py
├── openai_response_multiagent_multimodal.py
│
├── anthropic_call.py               # Anthropic Claude
├── anthropic_multiagent.py
├── anthropic_multimodal.py
├── anthropic_multiagent_multimodal.py
│
├── dashscope_call.py               # Alibaba DashScope / Qwen
├── dashscope_multiagent.py
├── dashscope_multimodal.py
├── dashscope_multiagent_multimodal.py
│
├── deepseek_call.py                # DeepSeek (no multimodal support)
├── deepseek_multiagent.py
│
├── gemini_call.py                  # Google Gemini
├── gemini_multiagent.py
├── gemini_multimodal.py
├── gemini_multiagent_multimodal.py
│
├── moonshot_call.py                 # Moonshot AI (Kimi)
├── moonshot_multiagent.py
├── moonshot_multimodal.py
├── moonshot_multiagent_multimodal.py
│
├── xai_call.py                     # xAI Grok
├── xai_multiagent.py
├── xai_multimodal.py
├── xai_multiagent_multimodal.py
│
├── ollama_call.py                  # Ollama local models (requires a running server)
├── ollama_multiagent.py
├── ollama_multimodal.py
└── ollama_multiagent_multimodal.py
```

---

## Test Types

| Suffix | File Pattern | What it covers |
|---|---|---|
| `call` | `*_call.py` | Basic text call + two-round tool calling |
| `multiagent` | `*_multiagent.py` | Multi-agent scenario using `MultiAgentFormatter` |
| `multimodal` | `*_multimodal.py` | Image + text multimodal input |
| `multiagent_multimodal` | `*_multiagent_multimodal.py` | Multi-agent + multimodal combined |

---

## Providers and Their Environment Variables

| Provider | Env Variable | Notes |
|---|---|---|
| `openai_chat` | `OPENAI_API_KEY` | Chat Completions API – gpt-4.1, etc. |
| `openai_response` | `OPENAI_API_KEY` | Responses API – o1, o3, o4-mini, etc. |
| `anthropic` | `ANTHROPIC_API_KEY` | Claude models, supports extended thinking |
| `dashscope` | `DASHSCOPE_API_KEY` | Qwen series, supports `thinking_enable` |
| `deepseek` | `DEEPSEEK_API_KEY` | Supports only `call` / `multiagent` (no multimodal) |
| `gemini` | `GEMINI_API_KEY` | Gemini models, supports `thinking_budget` |
| `moonshot` | `MOONSHOT_API_KEY` | Moonshot AI kimi-k2.6, etc. |
| `xai` | `XAI_API_KEY` | Grok models, supports `reasoning_effort` |
| `ollama` | *(none – auto-detect)* | Local server, default `http://localhost:11434` |

---

## Quick Start

### 1. Export API Keys

Set the environment variables for the providers you want to test:

```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export DASHSCOPE_API_KEY="sk-..."
export DEEPSEEK_API_KEY="sk-..."
export GEMINI_API_KEY="AIza..."
export MOONSHOT_API_KEY="sk-..."
export XAI_API_KEY="xai-..."
```

For Ollama, no API key is required. Just make sure the server is running:

```bash
ollama serve
ollama pull qwen3:14b   # pull the default model used in the scripts
```

### 2. Check Provider Availability

```bash
python scripts/model_examples/run_tests.py --list
```

Sample output:
```
  Provider               Env Var                   Available    Description
  openai_chat            OPENAI_API_KEY            YES          OpenAI Chat Completions API
  anthropic              ANTHROPIC_API_KEY          NO           Anthropic Claude models
  ...
```

### 3. Run All Available Tests

```bash
python scripts/model_examples/run_tests.py
```

The runner auto-detects which providers have credentials, skips those that do not, and runs all test types for the rest.

---

## `run_tests.py` Reference

```
usage: run_tests.py [-h] [--providers NAME[,NAME...]] [--tests TYPE[,TYPE...]]
                    [--timeout SECONDS] [--list] [--verbose]
```

### Options

| Option | Short | Default | Description |
|---|---|---|---|
| `--providers` | `-p` | all | Comma-separated list of providers to run |
| `--tests` | `-t` | all | Comma-separated list of test types to run |
| `--timeout` | | `120` | Per-script timeout in seconds |
| `--list` | `-l` | | Print provider status table and exit |
| `--verbose` | `-v` | | Stream each script's output in real time. By default output is suppressed and shown only when a test fails. |

### Examples

```bash
# Only test specific providers
python scripts/model_examples/run_tests.py --providers openai_chat,anthropic

# Only run a specific test type (across all available providers)
python scripts/model_examples/run_tests.py --tests call

# Combine: run call + multiagent tests for dashscope and deepseek
python scripts/model_examples/run_tests.py -p dashscope,deepseek -t call,multiagent

# Only run multimodal tests
python scripts/model_examples/run_tests.py --tests multimodal,multiagent_multimodal

# Increase per-script timeout
python scripts/model_examples/run_tests.py --timeout 180

# Check provider status
python scripts/model_examples/run_tests.py --list
```

### Summary Table

At the end of a run, a summary table is printed:

```
  Provider               Test Type                    Status      Time
  ---------------------- ---------------------------- -------- -------
  openai_chat            call                         PASS       12.3s
  openai_chat            multiagent                   PASS        8.1s
  anthropic              call                         SKIP      (env var ANTHROPIC_API_KEY not set)
  deepseek               call                         PASS       15.7s
  deepseek               multimodal                   SKIP      (not supported)

  Total: 12  |  PASS:   8  |  FAIL:   0  |  SKIP:   4
```

| Status | Meaning |
|---|---|
| **PASS** | Script exited with code 0 |
| **FAIL** | Script exited with a non-zero code or timed out |
| **SKIP** | API key missing, test type not supported, or script file absent |

The runner exits with code `1` if any test fails.

---

## Running a Single Script

Every script can be executed independently once the relevant environment variable is set:

```bash
python scripts/model_examples/openai_chat_call.py
python scripts/model_examples/dashscope_multiagent.py
python scripts/model_examples/ollama_multimodal.py
```

Each script typically defines two or more async functions:

- `example_simple_call()` – basic text call with streaming
- `example_tool_call()` – two-round conversation with tool/function calling
- `example_multimodal_call()` – image + text input (in `_multimodal.py` variants)

---

## Ollama Notes

Ollama runs locally and requires no API key, but you must:

1. Start the service: `ollama serve`
2. Pull the model used by the scripts: `ollama pull qwen3:14b`
3. If the service runs on a non-default address, set: `export OLLAMA_HOST=http://your-host:11434`

`run_tests.py` pings the Ollama host before running any test. If the server is unreachable, all Ollama tests are automatically skipped.

