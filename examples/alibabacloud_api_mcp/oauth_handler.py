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
            # 解析回调URL中的参数
            parsed_url = urlparse(self.path)
            params = parse_qs(parsed_url.query)
            
            if 'code' in params:
                # 获取授权码
                code = params['code'][0]
                state = params.get('state', [None])[0]
                
                # 存储结果
                self.callback_server.auth_code = code
                self.callback_server.auth_state = state
                self.callback_server.auth_received = True
                
                # 返回成功页面
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                
                success_html = """<!DOCTYPE html>
                <html lang="zh-CN">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>授权成功</title>
                </head>
                <body>
                    <h1>授权成功</h1>
                    <p>您已完成授权，可以返回应用继续使用。</p>
                    <p>窗口将在 <span id="countdown">3</span> 秒后自动关闭。</p>
                    <button onclick="window.close()">立即关闭</button>
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
                # 处理错误
                error = params['error'][0]
                error_description = params.get('error_description', ['Unknown error'])[0]
                
                self.callback_server.auth_error = f"{error}: {error_description}"
                self.callback_server.auth_received = True
                
                # 返回错误页面
                self.send_response(400)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                
                error_html = f"""<!DOCTYPE html>
                <html lang=\"zh-CN\">
                <head>
                    <meta charset=\"UTF-8\">
                    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
                    <title>授权失败</title>
                </head>
                <body>
                    <h1>授权失败</h1>
                    <p>在授权过程中发生错误。</p>
                    <p><strong>错误代码：</strong>{error}</p>
                    <p><strong>错误描述：</strong>{error_description}</p>
                    <button onclick=\"window.close()\">关闭窗口</button>
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
            <html lang=\"zh-CN\">
            <head>
                <meta charset=\"UTF-8\">
                <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
                <title>服务器错误</title>
            </head>
            <body>
                <h1>服务器内部错误</h1>
                <p>抱歉，服务器遇到了一个内部错误，无法完成您的请求。</p>
                <pre>{str(e)}</pre>
                <button onclick=\"window.close()\">关闭窗口</button>
            </body>
            </html>
            """
            self.wfile.write(internal_error_html.encode('utf-8'))
    
    def log_message(self, format, *args):
        """静默日志输出"""
        pass


class CallbackServer:
    """OAuth 回调服务器"""
    
    def __init__(self, port=3000):
        self.port = port
        self.server = None
        self.thread = None
        self.auth_code = None
        self.auth_state = None
        self.auth_error = None
        self.auth_received = False
    
    def start(self):
        """启动回调服务器"""
        handler = lambda *args, **kwargs: CallbackHandler(self, *args, **kwargs)
        self.server = HTTPServer(('localhost', self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"OAuth 回调服务器已启动，监听端口 {self.port}")
    
    def stop(self):
        """停止回调服务器"""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=1)
        print("OAuth 回调服务器已停止")
    
    async def wait_for_callback(self, timeout=300):
        """等待OAuth回调"""
        start_time = asyncio.get_event_loop().time()
        
        while not self.auth_received:
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise TimeoutError("等待OAuth回调超时")
            await asyncio.sleep(0.1)
        
        if self.auth_error:
            raise Exception(f"OAuth授权失败: {self.auth_error}")
        
        return self.auth_code, self.auth_state


# 全局回调服务器实例
_callback_server = None


async def handle_redirect(auth_url: str) -> None:
    """自动打开浏览器进行OAuth授权"""
    global _callback_server
    
    # 启动回调服务器
    if _callback_server is None:
        _callback_server = CallbackServer(port=3000)
        _callback_server.start()
    
    print(f"正在打开浏览器进行OAuth授权...")
    print(f"授权URL: {auth_url}")
    
    # 自动打开浏览器
    webbrowser.open(auth_url)


async def handle_callback() -> tuple[str, str | None]:
    """自动处理OAuth回调"""
    global _callback_server
    
    if _callback_server is None:
        raise Exception("回调服务器未启动")
    
    print("等待OAuth授权完成...")
    
    try:
        # 等待回调
        code, state = await _callback_server.wait_for_callback()
        print("OAuth授权成功!")
        return code, state
    
    except Exception as e:
        print(f"OAuth授权失败: {e}")
        raise
    
    finally:
        # 清理服务器状态，但保持服务器运行以便重用
        _callback_server.auth_code = None
        _callback_server.auth_state = None
        _callback_server.auth_error = None
        _callback_server.auth_received = False
