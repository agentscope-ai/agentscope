# -*- coding: utf-8 -*-
import asyncio
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthToken, OAuthClientInformationFull
from urllib.parse import parse_qs, urlparse


class InMemoryTokenStorage(TokenStorage):
    """Demo In-memory token storage implementation."""

    def __init__(self):
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

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Store client information."""
        self.client_info = client_info


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""
    
    def __init__(self, callback_server, *args, **kwargs):
        self.callback_server = callback_server
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET request for OAuth callback."""
        try:
            # Parse parameters from the callback URL
            parsed_url = urlparse(self.path)
            params = parse_qs(parsed_url.query)
            
            if 'code' in params:
                # Get authorization code
                code = params['code'][0]
                state = params.get('state', [None])[0]
                
                # Store results
                self.callback_server.auth_code = code
                self.callback_server.auth_state = state
                self.callback_server.auth_received = True
                
                # Return success page
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                
                success_html = """<!DOCTYPE html>
                <html lang="en">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Authorization Successful</title>
                </head>
                <body>
                    <h1>Authorization Successful</h1>
                    <p>You have completed authorization and can return to the application to continue using.</p>
                    <p>Window will automatically close in <span id="countdown">3</span> seconds.</p>
                    <button onclick="window.close()">Close Now</button>
                    <script>
                        let count = 3;
                        const el = document.getElementById('countdown');
                        const timer = setInterval(() => {
                            count--;
                            el.textContent = count;
                            if (count <= 0) {
                                clearInterval(timer);
                                window.close();
                            }
                        }, 1000);
                    </script>
                </body>
                </html>
                """
                self.wfile.write(success_html.encode('utf-8'))
                
            elif 'error' in params:
                # Handle error
                error = params['error'][0]
                error_description = params.get('error_description', ['Unknown error'])[0]
                
                self.callback_server.auth_error = f"{error}: {error_description}"
                self.callback_server.auth_received = True
                
                # Return error page
                self.send_response(400)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                
                error_html = f"""<!DOCTYPE html>
                <html lang=\"en\">
                <head>
                    <meta charset=\"UTF-8\">
                    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
                    <title>Authorization Failed</title>
                </head>
                <body>
                    <h1>Authorization Failed</h1>
                    <p>An error occurred during the authorization process.</p>
                    <p><strong>Error Code:</strong>{error}</p>
                    <p><strong>Error Description:</strong>{error_description}</p>
                    <button onclick=\"window.close()\">Close Window</button>
                </body>
                </html>
                """
                self.wfile.write(error_html.encode('utf-8'))
            
        except Exception as e:
            self.callback_server.auth_error = str(e)
            self.callback_server.auth_received = True
            
            self.send_response(500)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            
            internal_error_html = f"""<!DOCTYPE html>
            <html lang=\"en\">
            <head>
                <meta charset=\"UTF-8\">
                <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
                <title>Server Error</title>
            </head>
            <body>
                <h1>Internal Server Error</h1>
                <p>Sorry, the server encountered an internal error and could not complete your request.</p>
                <pre>{str(e)}</pre>
                <button onclick=\"window.close()\">Close Window</button>
            </body>
            </html>
            """
            self.wfile.write(internal_error_html.encode('utf-8'))
    
    def log_message(self, format, *args):
        """Silent log output"""
        pass


class CallbackServer:
    """OAuth callback server"""
    
    def __init__(self, port=3000):
        self.port = port
        self.server = None
        self.thread = None
        self.auth_code = None
        self.auth_state = None
        self.auth_error = None
        self.auth_received = False
    
    def start(self):
        """Start callback server"""
        handler = lambda *args, **kwargs: CallbackHandler(self, *args, **kwargs)
        self.server = HTTPServer(('localhost', self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"OAuth callback server started, listening on port {self.port}")
    
    def stop(self):
        """Stop callback server"""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=1)
        print("OAuth callback server stopped")
    
    async def wait_for_callback(self, timeout=300):
        """Wait for OAuth callback"""
        start_time = asyncio.get_event_loop().time()
        
        while not self.auth_received:
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise TimeoutError("OAuth callback timeout")
            await asyncio.sleep(0.1)
        
        if self.auth_error:
            raise Exception(f"OAuth authorization failed: {self.auth_error}")
        
        return self.auth_code, self.auth_state


# Global callback server instance
_callback_server = None


async def handle_redirect(auth_url: str) -> None:
    """Automatically open browser for OAuth authorization"""
    global _callback_server
    
    # Start callback server
    if _callback_server is None:
        _callback_server = CallbackServer(port=3000)
        _callback_server.start()
    
    print(f"Opening browser for OAuth authorization...")
    print(f"Authorization URL: {auth_url}")
    
    # Automatically open browser
    webbrowser.open(auth_url)


async def handle_callback() -> tuple[str, str | None]:
    """Automatically handle OAuth callback"""
    global _callback_server
    
    if _callback_server is None:
        raise Exception("Callback server not started")
    
    print("Waiting for OAuth authorization to complete...")
    
    try:
        # Wait for callback
        code, state = await _callback_server.wait_for_callback()
        print("OAuth authorization successful!")
        return code, state
    
    except Exception as e:
        print(f"OAuth authorization failed: {e}")
        raise
    
    finally:
        # Clean up server state but keep server running for reuse
        _callback_server.auth_code = None
        _callback_server.auth_state = None
        _callback_server.auth_error = None
        _callback_server.auth_received = False
