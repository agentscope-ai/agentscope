# Export AgentScope traces to MLflow

This example uses `TracingMiddleware` and OTLP/HTTP to send AgentScope agent,
model, and tool traces to an MLflow experiment.

## Start a local tracking server

Use Python 3.11+ and MLflow 3.6+ with a SQL backend. From the repository root,
install the dependencies and start a local server.

```sh
python -m pip install -e .
python -m pip install "mlflow>=3.6,<4"
mlflow server --host 127.0.0.1 --port 5000 \
  --backend-store-uri sqlite:///mlflow.db
```

Keep the server running. Open [MLflow](http://127.0.0.1:5000), create an
experiment named `agentscope-otlp-example`, and copy its experiment ID.

## Run the example

In another terminal, set `OPENAI_API_KEY` in your environment, then run from
the repository root with the experiment ID you copied.

```sh
python examples/mlflow/main.py --experiment-id YOUR_EXPERIMENT_ID
```

The default model is `gpt-4.1-mini`. For an OpenAI-compatible service, set
`OPENAI_BASE_URL` and pass `--model YOUR_MODEL`. To use another MLflow server,
pass `--traces-endpoint http://YOUR_HOST:5000/v1/traces`. Authenticated servers
also require configuring the exporter's headers and TLS settings.

## Expected result

The agent calls the `add` tool to calculate `17 + 25` and prints `42`.
Open the experiment's **Traces** view to inspect the `invoke_agent` span,
its `chat` and `execute_tool` children, and model token usage.

Traces include conversation messages, model request data, tool definitions,
arguments, and results. Review or filter this content before adapting the
example to private conversations.
