# -*- coding: utf-8 -*-
"""``build_agent()`` — the single fixed-vs-exposed seam (DESIGN.md §7).

This is the ONLY place agent construction lives. A forker copies the
example, edits this one function (model, system prompt, tool set,
permission rules), and leaves the ACP plumbing (``server.py``,
``session.py``, ``translate.py``, ``bridge.py``) untouched.
"""
import sys
from typing import TYPE_CHECKING

from agentscope.agent import Agent
from agentscope.permission import PermissionMode
from agentscope.tool import (
    BackendBase,
    Bash,
    Edit,
    Glob,
    Grep,
    Read,
    Toolkit,
    Write,
)

from .bridge import OpBindingMiddleware, OpRegistry
from .config import Config

if TYPE_CHECKING:
    from acp.interfaces import Client
    from acp.schema import ClientCapabilities

    from agentscope.model import ChatModelBase
    from agentscope.state import AgentState

_SYSTEM_PROMPT = """\
You are {name}, a general assistant with coding capabilities, running \
inside a desktop editor over the Agent Client Protocol.

The user's workspace directory is: {cwd}

Guidelines:
- Use the available tools to read, search, edit and run things in the \
workspace; prefer small, verifiable steps.
- File contents you read may include unsaved editor changes — treat \
them as the source of truth.
- Always use paths inside the workspace unless the user explicitly \
points elsewhere.
- Be concise: your replies stream directly into the editor's UI.\
"""

# Providers wired out of the box. Everything is resolved lazily so the
# example only needs the SDK of the provider actually selected.
# provider -> (model module attr, credential module attr, api-key env,
#              base-url env, default model name or None)
_PROVIDERS: dict[str, tuple[str, str, str, str, str | None]] = {
    "dashscope": (
        "DashScopeChatModel",
        "DashScopeCredential",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "qwen-max",
    ),
    "openai": (
        "OpenAIChatModel",
        "OpenAICredential",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        None,
    ),
    "anthropic": (
        "AnthropicChatModel",
        "AnthropicCredential",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        None,
    ),
    "deepseek": (
        "DeepSeekChatModel",
        "DeepSeekCredential",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        None,
    ),
    "moonshot": (
        "MoonshotChatModel",
        "MoonshotCredential",
        "MOONSHOT_API_KEY",
        "MOONSHOT_BASE_URL",
        None,
    ),
}


def _make_model(config: Config) -> "ChatModelBase":
    """Build the chat model strictly from environment configuration."""
    import os

    import agentscope.credential as credential_mod
    import agentscope.model as model_mod

    if config.model_provider not in _PROVIDERS:
        raise ValueError(
            f"Unsupported ACP_MODEL_PROVIDER={config.model_provider!r}; "
            f"choose one of {sorted(_PROVIDERS)} or edit build_agent().",
        )
    model_attr, cred_attr, key_env, url_env, default_name = _PROVIDERS[
        config.model_provider
    ]
    model_name = config.model_name or default_name
    if not model_name:
        raise ValueError(
            f"ACP_MODEL_NAME is required for provider "
            f"{config.model_provider!r}.",
        )
    api_key = os.environ.get(key_env)
    if not api_key:
        raise ValueError(
            f"{key_env} is required for provider {config.model_provider!r}.",
        )
    cred_kwargs: dict = {"api_key": api_key}
    if os.environ.get(url_env):
        cred_kwargs["base_url"] = os.environ[url_env]
    credential = getattr(credential_mod, cred_attr)(**cred_kwargs)
    return getattr(model_mod, model_attr)(
        credential=credential,
        model=model_name,
    )


def _make_workspace_backend(config: Config) -> BackendBase:
    """The opt-in sandbox backend (``ACP_AUTHORITY=workspace``).

    PR1 wires the local backend; Docker/E2B/Daytona/K8s/OpenSandbox/
    Bubblewrap/Apple-container backends are all ``BackendBase``
    subclasses with backend-specific connection settings — swap them in
    here (this is inside the fork seam).
    """
    if config.workspace_backend == "local":
        from agentscope.tool import LocalBackend

        return LocalBackend()
    raise ValueError(
        f"ACP_WORKSPACE_BACKEND={config.workspace_backend!r} is not wired "
        "in the example; edit _make_workspace_backend() in build_agent()'s "
        "module to construct the sandbox backend you need "
        "(agentscope.workspace exports Docker/E2B/Daytona/K8s/OpenSandbox/"
        "Bubblewrap/AppleContainer backends).",
    )


def _shell_tools(
    backend: BackendBase,
    cwd: str,
    caps: "ClientCapabilities | None",
    middleware: OpBindingMiddleware,
) -> list:
    """Gate client-delegated tools on the client's capabilities (§13).

    Every builtin file tool calls ``exec_shell`` (existence/dir/mkdir
    checks, ripgrep, the Glob helper), so *all* of them need the client
    ``terminal`` capability; Read/Write/Edit additionally need
    ``fs.readTextFile``/``fs.writeTextFile``. Never call a client
    method whose capability was not advertised — degrade by omitting
    the tool instead of silently touching the local disk.
    """
    fs = getattr(caps, "fs", None)
    fs_ok = bool(
        fs
        and getattr(fs, "read_text_file", False)
        and getattr(fs, "write_text_file", False),
    )
    term_ok = bool(getattr(caps, "terminal", False))
    mw = [middleware]
    tools: list = []
    if not term_ok:
        print(
            "acp_example: client lacks the 'terminal' capability — all "
            "client-delegated tools are disabled in shell mode; set "
            "ACP_AUTHORITY=workspace for kernel-owned tools.",
            file=sys.stderr,
        )
        return tools
    if fs_ok:
        tools += [
            Read(backend=backend, middlewares=mw),
            Write(backend=backend, middlewares=mw),
            Edit(backend=backend, middlewares=mw),
        ]
    else:
        print(
            "acp_example: client lacks fs.readTextFile/writeTextFile — "
            "Read/Write/Edit are disabled in shell mode.",
            file=sys.stderr,
        )
    tools += [
        Grep(backend=backend, middlewares=mw),
        Glob(backend=backend, middlewares=mw),
        Bash(cwd=cwd, backend=backend, middlewares=mw),
    ]
    return tools


def build_agent(
    *,
    cwd: str,
    state: "AgentState",
    conn: "Client",
    caps: "ClientCapabilities | None",
    ops: OpRegistry,
    config: Config,
) -> Agent:
    """Construct the example's fixed default agent.

    Args:
        cwd: The session working directory (absolute, from
            ``session/new``).
        state: The fresh ``AgentState`` whose ``session_id`` doubles as
            the ACP sessionId.
        conn: The ACP client-side connection (``acp.interfaces.Client``).
        caps: The initialize-time client capability snapshot.
        ops: The session's operation registry (receipt invariant c).
        config: The resolved environment configuration.
    """
    middleware = OpBindingMiddleware(ops)

    if config.is_shell_authority:
        # DEFAULT: shell delegation. ONE ClientBackend implements ALL
        # THREE BackendBase primitives: read_file/write_file -> fs/*,
        # exec_shell -> terminal/*.
        from .bridge import ClientBackend

        backend: BackendBase = ClientBackend(
            conn=conn,
            session_id=state.session_id,
            cwd=cwd,
            ops=ops,
        )
        tools = _shell_tools(backend, cwd, caps, middleware)
    else:
        # OPT-IN sandbox: one workspace backend implements all three
        # primitives natively; no client fs/terminal capability needed.
        backend = _make_workspace_backend(config)
        mw = [middleware]
        tools = [
            Read(backend=backend, middlewares=mw),
            Write(backend=backend, middlewares=mw),
            Edit(backend=backend, middlewares=mw),
            Grep(backend=backend, middlewares=mw),
            Glob(backend=backend, middlewares=mw),
            Bash(cwd=cwd, backend=backend, middlewares=mw),
        ]

    # Permission posture. DEFAULT gates Write/Edit/Bash behind ASK (with
    # a read-only fast path for Read/Grep/Glob), so no side effect ever
    # happens without exactly one client prompt (§12). Rules are
    # editable here by a forker.
    state.permission_context.mode = PermissionMode(config.permission_mode)

    return Agent(
        name=config.agent_name,
        system_prompt=_SYSTEM_PROMPT.format(name=config.agent_name, cwd=cwd),
        model=_make_model(config),
        toolkit=Toolkit(tools=tools),
        state=state,
    )
