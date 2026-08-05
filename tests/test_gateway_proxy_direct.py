"""Gateway proxy-direct transport tests.

Covers the host-side HTTP transport introduced for OpenSandbox
server-proxy (``AGENTSCOPE_GATEWAY_PROXY_DIRECT``):

* proxy-direct requests go straight over HTTP and never call the
  backend's ``exec_shell`` shim;
* network-level failures fall back to the in-sandbox shim;
* ``proxy_headers`` and the bearer token are merged into every
  proxy-direct request;
* HTTP-level errors (4xx/5xx) are NOT transport failures and do not
  trigger a fallback.
"""

import unittest
from unittest.mock import AsyncMock, patch

from agentscope.workspace._gateway_client import GatewayClient
from agentscope.tool import ExecResult

PROXY_BASE = "http://172.19.124.30:8101/v1/sandboxes/s1/proxy/5600"


class _FakeBackend:
    """Minimal backend recording ``exec_shell`` calls."""

    def __init__(self) -> None:
        self.exec_shell = AsyncMock()

    def __getattr__(self, name: str):
        # Legacy shim path may touch write_file / read_file /
        # remove_file; make them no-ops unless the test configures them.
        return AsyncMock()


class GatewayProxyDirectTest(unittest.IsolatedAsyncioTestCase):
    """Behaviour of the proxy-direct transport."""

    def _client(self, **kwargs) -> GatewayClient:
        self.backend = _FakeBackend()
        return GatewayClient(
            self.backend,
            5600,
            proxy_base_url=PROXY_BASE,
            proxy_headers={"X-Route": "abc"},
            auth_token="tok",
            **kwargs,
        )

    async def test_direct_request_hits_http_and_skips_shim(self) -> None:
        client = self._client()
        mock_request = AsyncMock(
            return_value=type("R", (), {"status_code": 200, "content": b'{"ok":true}'})()
        )
        with patch("httpx.AsyncClient.request", mock_request):
            status, body = await client.exec_request(
                "GET", "/health", include_auth=False
            )

        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"ok":true}')
        # Host-side transport: no in-sandbox spawn happened.
        self.backend.exec_shell.assert_not_awaited()
        # URL assembled from proxy base + gateway port.
        url = mock_request.await_args.args[1]
        self.assertEqual(url, f"{PROXY_BASE}/health")
        kwargs = mock_request.await_args.kwargs
        self.assertEqual(kwargs["headers"]["X-Route"], "abc")
        # include_auth=False → no bearer token on health probes.
        self.assertNotIn("Authorization", kwargs["headers"])

    async def test_direct_request_sends_bearer_when_included(self) -> None:
        client = self._client()
        mock_request = AsyncMock(
            return_value=type("R", (), {"status_code": 200, "content": b"{}"})()
        )
        with patch("httpx.AsyncClient.request", mock_request):
            await client.exec_request("POST", "/mcps/m/tools/t", body={"q": 1})

        kwargs = mock_request.await_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(kwargs["json"], {"q": 1})

    async def test_transport_failure_falls_back_to_shim(self) -> None:
        client = self._client()
        # Network-level failure (connection refused).
        def _boom(*_a, **_k):
            raise OSError("connection refused")

        self.backend.exec_shell.return_value = ExecResult(
            exit_code=1, stdout=b"", stderr=b"shim crashed"
        )
        with patch("httpx.AsyncClient.request", _boom):
            with self.assertRaises(RuntimeError):
                await client.exec_request("GET", "/mcps")

        # Fallback engaged: the in-sandbox shim was spawned.
        self.backend.exec_shell.assert_awaited()

    async def test_http_error_does_not_fall_back(self) -> None:
        client = self._client()
        # The gateway answers 500: that is a real HTTP response, not a
        # transport failure — falling back to the shim would hit the
        # same gateway, so we must NOT spawn it.
        mock_request = AsyncMock(
            return_value=type("R", (), {"status_code": 500, "content": b"boom"})()
        )
        with patch("httpx.AsyncClient.request", mock_request):
            status, body = await client.exec_request("GET", "/mcps/m/tools/t")

        self.assertEqual(status, 500)
        self.assertEqual(body, b"boom")
        self.backend.exec_shell.assert_not_awaited()

    async def test_no_proxy_base_uses_shim_directly(self) -> None:
        backend = _FakeBackend()
        backend.exec_shell.return_value = ExecResult(
            exit_code=1, stdout=b"", stderr=b"boom"
        )
        client = GatewayClient(backend, 5600)  # proxy_base_url=None
        with self.assertRaises(RuntimeError):
            await client.exec_request("GET", "/mcps")
        backend.exec_shell.assert_awaited()


if __name__ == "__main__":
    unittest.main()
