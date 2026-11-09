# -*- coding: utf-8 -*-
"""Credential endpoint resolution shared by enrollment and synthesis."""
from urllib.parse import urlsplit

from ...credential import DashScopeCredential


def _qwen_http_url(credential: DashScopeCredential, path: str) -> str:
    """Share Qwen enrollment/synthesis routing without changing SDK globals."""
    if "base_url" in credential.model_fields_set:
        return _dashscope_url(credential.base_url, path)

    import dashscope

    parsed = urlsplit(dashscope.base_http_api_url)
    return parsed._replace(
        path=parsed.path.rstrip("/") + path.removeprefix("/api/v1"),
        fragment="",
    ).geturl()


def _dashscope_url(
    base_url: str,
    path: str,
    *,
    websocket: bool = False,
) -> str:
    """Replace the API suffix, preserving the host and proxy path prefix."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("DashScope base_url must be an absolute HTTP(S) URL")
    prefix = parsed.path.rstrip("/")
    for suffix in ("/compatible-mode/v1", "/api/v1"):
        if prefix.endswith(suffix):
            prefix = prefix[: -len(suffix)]
            break
    scheme = parsed.scheme
    if websocket:
        scheme = "wss" if scheme == "https" else "ws"
    return parsed._replace(
        scheme=scheme,
        path=prefix + path,
        fragment="",
    ).geturl()
