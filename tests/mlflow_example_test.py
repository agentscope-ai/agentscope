# -*- coding: utf-8 -*-
"""Exercise the MLflow example through real HTTP exporter/model boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import TestCase

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/mlflow/main.py"


class MLflowExampleTest(TestCase):
    """Catch missing OTLP routing, experiment headers, or shutdown flushing."""

    def setUp(self) -> None:
        self.requests: list[tuple[str, dict, bytes]] = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            """Serve a scripted OpenAI response and capture real OTLP bytes."""

            def do_POST(self) -> None:
                """Return a tool call followed by its final answer."""
                body = self.rfile.read(int(self.headers["Content-Length"]))
                requests.append((self.path, dict(self.headers), body))
                if self.path == "/v1/traces":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-protobuf")
                    self.end_headers()
                    return
                if self.path != "/openai/v1/chat/completions":
                    self.send_error(404)
                    return
                messages = json.loads(body)["messages"]
                called_tool = any(m["role"] == "tool" for m in messages)
                message: dict[str, object] = {
                    "role": "assistant",
                    "content": "42",
                }
                if not called_tool:
                    message = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "add-call",
                                "type": "function",
                                "function": {
                                    "name": "add",
                                    "arguments": '{"a":17,"b":25}',
                                },
                            },
                        ],
                    }
                payload = {
                    "id": "response-2" if called_tool else "response-1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "demo-chat",
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "stop"
                            if called_tool
                            else "tool_calls",
                        },
                    ],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 3,
                        "total_tokens": 14,
                    },
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, *_args: object) -> None:
                """Keep expected local requests out of test output."""

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _run(self, api_key: str) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            OPENAI_API_KEY=api_key,
            OPENAI_BASE_URL=f"{self.url}/openai/v1",
            NO_PROXY="127.0.0.1",
        )
        return subprocess.run(
            [
                sys.executable,
                str(EXAMPLE),
                "--experiment-id",
                "42",
                "--traces-endpoint",
                f"{self.url}/v1/traces",
                "--model",
                "demo-chat",
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def test_cli_exports_agent_model_and_tool_to_selected_experiment(
        self,
    ) -> None:
        """The example must export its finished trace before process exit."""
        result = self._run("local-test-key")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("42", result.stdout)
        exports = [r for r in self.requests if r[0] == "/v1/traces"]
        self.assertTrue(exports, result.stderr)
        spans = []
        for _, headers, body in exports:
            headers = {k.lower(): v for k, v in headers.items()}
            self.assertEqual(headers["x-mlflow-experiment-id"], "42")
            self.assertEqual(headers["content-type"], "application/x-protobuf")
            request = ExportTraceServiceRequest.FromString(body)
            for resource in request.resource_spans:
                for scope in resource.scope_spans:
                    spans.extend(scope.spans)
        names = [s.name for s in spans]
        self.assertEqual(names.count("chat demo-chat"), 2, names)
        self.assertIn("execute_tool add", names)
        roots = [s for s in spans if not s.parent_span_id]
        self.assertEqual(len(roots), 1, names)
        self.assertTrue(roots[0].name.startswith("invoke_agent"))
        self.assertTrue(all(s.trace_id == roots[0].trace_id for s in spans))
        self.assertTrue(
            all(
                s.parent_span_id == roots[0].span_id
                for s in spans
                if s not in roots
            ),
        )
        model_span = next(s for s in spans if s.name == "chat demo-chat")
        usage = {a.key: a.value.int_value for a in model_span.attributes}
        self.assertEqual(usage["gen_ai.usage.input_tokens"], 11)
        self.assertEqual(usage["gen_ai.usage.output_tokens"], 3)

    def test_missing_model_key_fails_before_network_calls(self) -> None:
        """A missing model credential must produce an actionable CLI error."""
        result = self._run("")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OPENAI_API_KEY", result.stderr)
        self.assertEqual(self.requests, [])
