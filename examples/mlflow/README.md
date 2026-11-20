# Export AgentScope traces to MLflow

This example sends AgentScope 2.x agent, model, and tool spans to an MLflow
experiment using the existing `TracingMiddleware` and OpenTelemetry's
OTLP/HTTP exporter. MLflow is only needed for the tracking server and UI;
the example adds no dependency to AgentScope.

This is the tracing slice of [#1458](https://github.com/agentscope-ai/agentscope/issues/1458).
It does not create MLflow experiment runs, log run parameters/metrics or
artifacts, estimate cost, or implement the older `MLflowLogger` API sketch.
Token usage and latency are trace attributes/timings, not run metrics.

## Start a local tracking server

Use Python 3.11+ and install AgentScope from the repository root:

```sh
python -m pip install -e .
python -m pip install "mlflow>=3.6,<4"
mlflow server --host 127.0.0.1 --port 5000 \
  --backend-store-uri sqlite:///mlflow.db
```

Keep the server running. In another terminal, create an experiment:

```sh
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 \
  mlflow experiments create --experiment-name agentscope-otlp-example
```

Copy the returned experiment ID. You can also create an experiment in the
UI at <http://127.0.0.1:5000> and copy its ID.

MLflow's [OTLP ingestion](https://mlflow.org/docs/latest/genai/tracing/opentelemetry/ingest/)
requires **MLflow 3.6.0 or newer**, a **SQL backend**, and **OTLP/HTTP protobuf**.
Its endpoint is `/v1/traces`; gRPC is not supported. Each export includes the
required `x-mlflow-experiment-id` header. The example disables compression
because compressed requests require MLflow 3.7 or newer.

## Run the example

Set `OPENAI_API_KEY` in your environment, then run from the repository root:

```sh
python examples/mlflow/main.py --experiment-id YOUR_EXPERIMENT_ID
```

The example asks the model to call a read-only addition tool and prints the
answer (`42`). Open the experiment's **Traces** view to inspect the
`invoke_agent` span with its `chat` and `execute_tool` children.

For an OpenAI-compatible service, set `OPENAI_BASE_URL` and select its model:

```sh
python examples/mlflow/main.py --experiment-id YOUR_EXPERIMENT_ID \
  --model YOUR_MODEL
```

For a different MLflow host, pass its full OTLP endpoint with
`--traces-endpoint http://YOUR_HOST:5000/v1/traces`. These instructions target
a local server without authentication; configure the exporter's headers
and TLS settings for your own authenticated deployment.

## What is exported

The current `TracingMiddleware` includes conversation messages, model
request data, tool definitions, tool arguments, and tool results.
The example uses fictional arithmetic inputs. Review or filter those
attributes before adapting it to private conversations; content export is
not opt-in here. Model credentials are used for the model connection and
are not configured as exporter headers.

The batch processor exports on a worker thread. The standalone program owns
its global tracer provider and flushes/shuts it down in `finally`.
When integrating into an existing application, add the processor to that
application's provider instead of replacing its global provider.

A successful `force_flush()` means the queue drained, not that MLflow
accepted the spans. Check exporter errors and confirm the trace appears in
MLflow. Common causes of a missing trace are an incorrect experiment ID,
omitting `/v1/traces`, using a file-based backend, or an unsupported server.

## Validation

The CLI test runs a real AgentScope agent and OpenAI adapter against a local,
scripted HTTP model endpoint. It captures actual OTLP protobuf exports and
checks the experiment header, trace hierarchy, usage attributes, and export
before process exit. It makes no paid model calls:

```sh
python -m pytest tests/mlflow_example_test.py -q
```

This boundary test does not start MLflow itself; acceptance by an actual
MLflow server must be verified separately in its Traces view or through
`mlflow.search_traces`.
