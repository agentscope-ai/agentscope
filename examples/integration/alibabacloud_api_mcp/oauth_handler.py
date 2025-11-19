# -*- coding: utf-8 -*-
"""OAuth handler utilities for the Alibaba Cloud MCP example."""

from __future__ import annotations

import asyncio
import socket
import threading
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from textwrap import dedent
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

SUCCESS_PAGE = dedent(
    """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Authorization Complete</title>
    </head>
    <body>
        <h1>Authorization Complete</h1>
        <p>You can now return to the application.</p>
        <button onclick="window.close()">Close Window</button>
    </body>
    </html>
    """,
)

ERROR_PAGE_TEMPLATE = dedent(
    """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Authorization Error</title>
    </head>
    <body>
        <h1>Authorization Error</h1>
        <p><strong>Code:</strong> {error}</p>
        <p><strong>Description:</strong> {description}</p>
        <button onclick="window.close()">Close Window</button>
    </body>
    </html>
    """,
)

INTERNAL_ERROR_TEMPLATE = dedent(
    """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Server Error</title>
    </head>
    <body>
        <h1>Server Error</h1>
        <p>Sorry, something went wrong while handling the callback.</p>
        <pre>{details}</pre>
        <button onclick="window.close()">Close Window</button>
    </body>
    </html>
    """,
)


class InMemoryTokenStorage(TokenStorage):
    """Demo in-memory token storage implementation."""

    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        """Get stored tokens."""
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Store tokens."""
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Get stored client information."""
        return self.client_info

    async def set_client_info(
        self,
        client_info: OAuthClientInformationFull,
    ) -> None:
        """Store client information."""
        self.client_info = client_info


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""

    def __init__(
        self,
        callback_server: "CallbackServer",
        request: socket.socket,
        client_address: tuple[str, int],
        server: HTTPServer,
    ) -> None:
        self.callback_server: "CallbackServer" = callback_server
        super().__init__(request, client_address, server)

    def do_GET(self) -> None:
        """Handle GET request for OAuth callback."""
        try:
            parsed_url = urlparse(self.path)
            params = parse_qs(parsed_url.query)

            if "code" in params:
                code = params["code"][0]
                state = params.get("state", [None])[0]

                self.callback_server.auth_code = code
                self.callback_server.auth_state = state
                self.callback_server.auth_received = True

                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(SUCCESS_PAGE.encode("utf-8"))

            elif "error" in params:
                error = params["error"][0]
                description = params.get(
                    "error_description",
                    ["Unknown error"],
                )[0]

                self.callback_server.auth_error = f"{error}: {description}"
                self.callback_server.auth_received = True

                self.send_response(400)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                page = ERROR_PAGE_TEMPLATE.format(
                    error=error,
                    description=description,
                )
                self.wfile.write(page.encode("utf-8"))

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.callback_server.auth_error = str(exc)
            self.callback_server.auth_received = True

            self.send_response(500)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()

            page = INTERNAL_ERROR_TEMPLATE.format(details=exc)
            self.wfile.write(page.encode("utf-8"))


class CallbackServer:
    """OAuth callback server."""

    def __init__(self, port: int = 3000) -> None:
        self.port = port
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.auth_code: str | None = None
        self.auth_state: str | None = None
        self.auth_error: str | None = None
        self.auth_received = False

    def start(self) -> None:
        """Start callback server."""

        handler = partial(CallbackHandler, self)
        self.server = HTTPServer(("localhost", self.port), handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        print(f"OAuth callback server started, listening on port {self.port}")

    def stop(self) -> None:
        """Stop callback server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=1)
        print("OAuth callback server stopped")

    async def wait_for_callback(
        self,
        timeout: float = 300,
    ) -> tuple[str, str | None]:
        """Wait for OAuth callback."""

        loop = asyncio.get_running_loop()
        start_time = loop.time()

        while not self.auth_received:
            if loop.time() - start_time > timeout:
                raise TimeoutError("OAuth callback timeout")
            await asyncio.sleep(0.1)

        if self.auth_error:
            raise RuntimeError(
                f"OAuth authorization failed: {self.auth_error}",
            )

        if self.auth_code is None:
            raise RuntimeError(
                "OAuth authorization failed: missing authorization code",
            )

        return self.auth_code, self.auth_state


# Global callback server instance
_callback_server: CallbackServer | None = None


async def handle_redirect(auth_url: str) -> None:
    """Automatically open browser for OAuth authorization."""
    global _callback_server

    # Start callback server
    if _callback_server is None:
        _callback_server = CallbackServer(port=3000)
        _callback_server.start()

    print("Opening browser for OAuth authorization...")
    print(f"Authorization URL: {auth_url}")

    # Automatically open browser
    webbrowser.open(auth_url)


async def handle_callback() -> tuple[str, str | None]:
    """Automatically handle OAuth callback."""
    global _callback_server

    if _callback_server is None:
        raise RuntimeError("Callback server not started")

    print("Waiting for OAuth authorization to complete...")

    try:
        # Wait for callback
        code, state = await _callback_server.wait_for_callback()
        print("OAuth authorization successful!")
        return code, state

    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"OAuth authorization failed: {e}")
        raise

    finally:
        # Clean up server state but keep server running for reuse
        _callback_server.auth_code = None
        _callback_server.auth_state = None
        _callback_server.auth_error = None
        _callback_server.auth_received = False
