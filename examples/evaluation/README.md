# Trajectory Evaluation

Collect one Agent reply as an event trajectory, score explicit expectations,
and export a local JSONL report. Collection does not change the Agent, its
tools, or permission decisions.

## Run the offline example

From the repository root, after installing the project:

```bash
python examples/evaluation/main.py
python examples/evaluation/main.py --output evaluation-results.jsonl
```

No API key or model download is required. Both fixture tools return
successfully: `tool_result_success_rate` is `1.0`. The wrong tool's
`tool_selection_accuracy` is `0.0`. The final-text oracle accepts both fixtures
because their final text is identical; use a richer predicate when success
also depends on actions or state. This is a protocol demo, not a model benchmark.

## Connect a real Agent

The callback owns Agent creation and credentials. Create a fresh Agent for
each independent case and return its existing event stream:

```python
from agentscope.evaluation import (
    EvaluationCase,
    EvaluationRunner,
    TrajectoryEvaluator,
    write_jsonl,
)
from agentscope.message import UserMsg


def execute(case):
    agent = build_agent()  # Your application factory and configuration.
    return agent.reply_stream(case.inputs, yield_final_msg=True)


async def evaluate():
    cases = [
        EvaluationCase(
            id="weather-london",
            inputs=[UserMsg("user", "Weather in London?")],
            reference={
                "expected_tools": ["weather"],
                "final_text": "sunny",
            },
        ),
    ]
    runner = EvaluationRunner(execute, [TrajectoryEvaluator()])
    results = await runner.run(cases)
    write_jsonl(results, "evaluation-results.jsonl")
    return results
```

`build_agent()` is supplied by the application. This example omits tool
schemas, so argument validity is unavailable. Supply `case.tool_schemas` as
OpenAI-style function schemas, or dictionaries with `name` and `parameters`,
to enable it. The evaluator checks this supplied snapshot; it does not
discover dynamically changing tool schemas.

## Golden references and metrics

```json
{
  "expected_tools": ["search", "search", "summarize"],
  "final_text": "The expected final answer"
}
```

- `expected_tools` is an exhaustive, **order-independent multiset** of tool
  names. Repeated names specify expected occurrences, including permitted
  retries or pagination. The built-in matcher does not validate order or
  the meaning of arguments.
- `tool_selection_accuracy` is matched name occurrences divided by observed
  complete tool requests. Missing expected calls are reported in
  `details.unmatched_expected`; this metric is not task success.
- `redundant_tool_call_rate` measures surplus occurrences relative to that
  exhaustive reference. Repeated names alone do not establish redundant work.
- `final_text` checks exact final-message text after the reply terminates.
  A terminated error or interruption fails this oracle.
- `tool_result_success_rate` uses observed terminal result states. Denied,
  error, and interrupted results count as non-success; running or missing
  results are excluded and reported separately. This is neither a pure I/O
  execution-success metric nor semantic correctness.
- `argument_validity_rate` checks observed JSON arguments against supplied
  JSON Schemas. Unknown schemas are excluded and reported. Valid syntax does
  not establish that arguments are appropriate for the task.

Rates expose numerator and denominator. Missing evidence produces Python
`None`, serialized as JSON `null`, with a reason. Empty samples never receive
an automatic perfect score. Without `expected_tools`, selection correctness
and redundancy are unavailable.

For richer task success, inject a synchronous or asynchronous predicate:

```python
def task_success(trajectory, case):
    if trajectory.finished_reason is None:
        return None
    # Inspect copied events, tool results, and task-specific reference data.
    return trajectory.output is not None and "sunny" in (
        trajectory.output.get_text_content() or ""
    )


evaluator = TrajectoryEvaluator(task_success=task_success)
```

The predicate receives isolated copies, returns `bool` or `None`, and takes
precedence over `final_text`. For other matching rules, implement
`EvaluatorBase.evaluate()` and return your own `MetricResult` objects.

## Human input and partial streams

One case represents **one logical reply**, including its resumes. Exhaustion
of a stream parked for human confirmation or external execution is not task
completion. Reuse the same `TrajectoryCollector` for subsequent streams with
the same `reply_id`; do not start another reply with a new user message.

The runner callback may concatenate streams that resume that reply. The
caller obtains actual confirmation or external results; the runner never
approves actions or fabricates those inputs. A callback ending while parked
leaves `awaiting_input=True` and may produce unavailable metrics.

For direct collection, call `collector.record(item)` for each event or final
message, then `collector.end_segment("exhausted")`. Continue recording into
the same collector on resumption. `snapshot()` returns a deep copy without
ending the reply. Different session/reply identifiers are rejected.

`collection_status` describes the subscription; `finished_reason` describes
observed Agent termination. Cancellation propagates from `runner.run()` after
saving the partial trajectory. `runner.snapshot()` retains all started cases,
including the cancelled subscription. Cancelling collection alone does not
prove that the Agent itself was interrupted.

## Measurement limits

- Event IDs are deduplicated. Orphaned deltas and activity after termination
  remain in raw events with diagnostics. Missing results are not silently
  converted into success or failure.
- `reply` is rebuilt from events with intermediate tool blocks; `output` is
  the explicit final `Msg`. Use `yield_final_msg=True` to collect the latter.
  Missing final output alone does not establish failure.
- `reported_usage` sums unique observed `ModelCallEndEvent` values only.
  Final-message usage is not added again. Events may omit compression calls
  and internal retries, and cannot distinguish unavailable usage encoded as
  zero. `usage_verified` is always `False`.
- Events lack model attempt IDs and may not identify the actual fallback
  model. Reports contain neither exact attempt counts nor billing costs.
- `duration_ms` is observed lifecycle time, including human waiting, not pure
  model or tool I/O latency. Inadequate or inconsistent timestamps give `null`.
- Schema references must be local fragments such as `#/$defs/City`. Remote
  and relative external references are rejected; no schema is fetched.

## JSONL export

`write_jsonl(results, path)` writes one result per UTF-8 line, preserving nulls
and diagnostics. The parent directory must exist. Existing files raise
`FileExistsError`; use `overwrite=True` explicitly to replace a report.
Serialization finishes before the destination is opened, so serialization
errors cannot truncate an existing report. Filesystem errors propagate.
