# -*- coding: utf-8 -*-
"""HTTP client for calling CodeActToolCallServer."""


async def remote_tool_call(
    server_host: str,
    server_port: int,
    tool_name: str,
    tool_args: dict | None = None,
    timeout: float = 60,
) -> dict:
    """Call a CodeActToolCallServer to run the requested tool and return
    the structured output.

    Args:
        server_host (`str`):
            The host address of the CodeActToolCallServer.
        server_port (`int`):
            The port of the CodeActToolCallServer.
        tool_name (`str`):
            The name of the tool function to call.
        tool_args (`dict | None`, optional):
            The arguments to pass to the tool function.
        timeout (`float`, defaults to `60`):
            The request timeout in seconds.

    Returns:
        `dict`:
            The output returned by the server, or an empty dict on error.
    """
    import httpx

    url = f"http://{server_host}:{server_port}/call_tool"
    payload = {"tool_name": tool_name, "tool_args": tool_args or {}}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            if resp and resp.raise_for_status():
                return resp.json() or {}

            raise ValueError("no response from code act server")
    except Exception as e:
        raise ValueError("cannot get tool result") from e
