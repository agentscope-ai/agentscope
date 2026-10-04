# Task-aware tool selection

Run from an editable checkout with the project's normal dependencies installed:

```bash
python examples/tool_selection/main.py --tasks 30
```

The example needs no API key, network connection or dependency on the test
suite. It first runs a real `Agent.reply()` through an offline chat model and
prints the schemas reaching the model. The relevant constructor arguments are:

```python
agent = Agent(
    # Existing model, toolkit, name and prompt arguments...
    tool_selector=EmbeddingToolSelector(embedding_model, top_k=3),
    max_tool_schema_tokens=450,
    required_tools=("audit_notes",),
)
```

`agent.last_tool_selection` reports the selected schemas, estimated schema
tokens, retrieval fallback and budget overflow. The selector is optional;
omitting it preserves the original all-available-tools behavior. Registration,
tool-group activation and execution permissions remain owned by the existing
toolkit and agent.

## Reading the measurements

For each of 20, 50 and 100 tools, the script creates 30 synthetic tasks with
explicit golden tool-name sets. Each task asks for two capabilities. Recall is
the fraction of those golden tools present in the selected schemas. `top_k=3`
allows up to three optional tools in addition to the required control tool.

The fixture embedding matches explicit capability IDs. Its high recall is a
protocol check, **not evidence of semantic retrieval quality on real tasks**.
The offline chat model only reports visible schemas; it does not execute tasks.
Accordingly `task_success_rate` is null. Evaluate task success separately with
real workflows and outcome assertions before making production quality claims.

The script reports estimated schema tokens using `ChatModelBase.count_tokens`.
Its default counter estimates UTF-8 bytes rather than using a provider
tokenizer. Cold latency includes embedding every optional candidate; warm
latency reuses schema vectors and embeds only the query. These are local
fixture timings with no simulated network delay; they do not predict API
latency or guarantee a warm-cache speedup. Embedded-input counts demonstrate
the cache behavior independently of timing noise.

## Budget and failure behavior

- Required tools, explicitly forced tool names and every name in
  `ToolChoice.tools` survive selection. They consume the normal schema budget.
  If required schemas already exceed that budget, selection raises an error.
- Optional tools are ranked by cosine similarity and greedily admitted when
  they fit. Returned schemas retain their original candidate order.
- Empty queries skip embedding and select in candidate order within budget.
- By default, an embedding request failure or invalid vectors restore **all
  current candidates**, even above budget. Diagnostics identify this fallback.
  Use `failure_policy="raise"` to propagate retrieval failures instead.
- Token-counter errors, invalid constraints and cancellation propagate.
  A full-tools fallback does not guarantee that the model context will fit.

The agent prepares the selection before context accounting and reuses a
snapshot during the reasoning step. Later model-call middleware may replace
the model or tools; those changes are outside this selector's budget. Accounting
uses the primary model's counter; a configured fallback model can have a
different tokenizer or context window and is not covered by that estimate.

## Using a real embedding model

Replace `FixtureEmbedding()` with an existing text `EmbeddingModelBase`, for
example an `OpenAIEmbeddingModel` configured with its usual credential, model
name and dimensions. This example does not instantiate or call that provider.
No vector database is required.

Schema embeddings are held in a bounded memory cache. Schema content, model
configuration, credential replacement and endpoint configuration invalidate
cache entries. API secrets are not unwrapped or stored in the cache. Call
`selector.clear_cache()` after changing hidden provider behavior or mutating
only the secret inside an existing credential object.
