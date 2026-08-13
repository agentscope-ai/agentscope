# -*- coding: utf-8 -*-
"""Host-side helpers shared by workspace implementations.

Constants for the standard workspace layout, plus pure functions for
detecting the local ``agentscope`` version and reading scripts bundled
with the package. No Docker / E2B SDK dependency lives here.

This module is internal to ``agentscope.workspace``. Public-sounding
constants are shared within the package, not exported as user-facing
API.
"""

import importlib.resources as _res

# ── shared constants ───────────────────────────────────────────────

#: Standard prompt injected to the system prompt
DEFAULT_WORKSPACE_INSTRUCTIONS = """<workspace>你可以在 {workdir} 使用一个 {backend} \
工作区，其结构如下：

```
{workdir}
├── data/        # 卸载的多模态文件（图片等）——系统管理
├── skills/      # 可复用的技能，每个技能位于各自的子目录中
└── sessions/    # 卸载的会话上下文与工具结果——系统管理
```

这个工作区是你的个人工作环境。你有责任保持它整洁、结构清晰、长期便于浏览。

### 项目目录
- 在工作区根目录下，为每个任务或项目创建专门的子目录。
- 每个项目子目录的命名应简洁且具有描述性，并以绝对创建日期作为前缀，如 \
`20240315_web-scraper`，以便在创建很久之后仍可辨识。
- 始终在项目根目录创建 `README.md`，记录：
  - 项目的内容是什么
  - 项目的绝对创建日期
  - 有助于你之后恢复工作的关键决策或背景

### 跨会话协作
- 同一项目可能同时被多个会话处理。这里没有实时锁来提示你另一个会话正在 \
编辑某个文件——避免冲突要靠隔离，而不是靠侥幸：
  - 优先使用带会话专属名称的 `git worktree`，使并行工作在各自独立的工作 \
树上进行，绝不共享同一批文件。
  - 在名称中编码归属信息（创建日期、会话标识），以明确哪个会话创建了什么。
- 删除时要保守：不要删除任何不是你本会话创建的内容，优先归档而非删除，并 \
依赖 git 以便任何变更都能回滚。进行破坏性清理前请先确认。

### 临时 / 暂存文件
- 将一次性实验、中间数据以及任何你原本会丢进 `/tmp` 的内容，放在 `scratch/` \
目录（首次使用时创建）下，而不是放在项目目录内——这能让项目及其 git 历史 \
保持干净。
- 将 `scratch/` 视为一次性内容：将其排除在 git 之外，不要假设其中的任何 \
内容都能持久保存。没有任何东西会自动清理它（它位于你的持久化工作区内，而 \
不是操作系统临时目录），因此用完自己的临时文件后请自行删除。

### 版本控制
- 建议在每个项目目录中初始化 `git` 仓库，以跟踪变更并支持回滚。
- 如果使用 git，请在首次提交前创建 `.gitignore`，以排除不需要的文件（如 \
虚拟环境、缓存、`scratch/`、密钥）。
- 绝不要将密钥硬编码进项目文件或提交它们——这是一个个人环境，但请把凭据 \
当作可能泄露来处理。

### Python 环境
- 建议使用 `uv` 来为每个项目管理和隔离 Python 环境：
```shell
uv venv && uv pip install ...
- 绝不要将包安装到共享或全局环境中——每个项目 \
都必须自行管理依赖，以避免冲突。</workspace>"""

#: Standard workspace-relative directory for offloaded multimodal data.
DEFAULT_DATA_DIR = "data"

#: Standard workspace-relative directory for reusable skills.
DEFAULT_SKILLS_DIR = "skills"

#: Standard workspace-relative directory for session context and results.
DEFAULT_SESSIONS_DIR = "sessions"

#: Standard workspace-relative file for persisted MCP registrations.
DEFAULT_MCP_FILE = ".mcp"

DEFAULT_GATEWAY_VENV = ".venv"
DEFAULT_GATEWAY_LOG = "gateway.log"
DEFAULT_GATEWAY_SCRIPT = "_mcp_gateway_app.py"
DEFAULT_GLOB_HELPER_SCRIPT = "_glob_helper.py"

#: Minimum Python packages the gateway script needs at runtime.
#: Both Docker (image build) and E2B (sandbox bootstrap) install this
#: same tuple into the gateway venv before adding ``agentscope`` itself.
#:
#: ``agentscope`` goes in with ``--no-deps``, so anything it imports has
#: to be named here — depending on another package to drag it along is
#: what broke when ``mcp`` 2.0 swapped ``httpx`` for ``httpx2``. The
#: bound on ``mcp`` mirrors ``pyproject.toml``, so the gateway speaks
#: the same protocol version as the process driving it.
_GATEWAY_BASE_REQUIREMENTS: tuple[str, ...] = (
    "mcp<2.0.0",
    "uvicorn",
    "fastapi",
    "httpx",
)


# ── gateway script ─────────────────────────────────────────────────


def _read_gateway_script_bytes() -> bytes:
    """Read the standalone gateway script as bytes via ``importlib.resources``.

    The script ships at
    ``agentscope/workspace/_mcp_gateway/_mcp_gateway_app.py``. Both
    backends copy it to a fixed in-container / in-sandbox path so the
    launch command can invoke it directly, avoiding ``python -m`` and
    the heavy ``agentscope.workspace.__init__`` import graph.
    """
    return (
        _res.files("agentscope.workspace._mcp_gateway")
        .joinpath("_mcp_gateway_app.py")
        .read_bytes()
    )


# ── builtin tool helper scripts ───────────────────────────────────


def _read_glob_helper_bytes() -> bytes:
    """Read the standalone glob helper script as bytes.

    The script ships at
    ``agentscope/tool/_builtin/_scripts/_glob_helper.py``. Both Docker
    and E2B backends copy it into the workspace so the :class:`Glob`
    tool can invoke it uniformly via ``exec_shell``.

    Returns:
        `bytes`:
            The raw contents of the ``_glob_helper.py`` script.
    """
    return (
        _res.files("agentscope.tool._builtin._scripts")
        .joinpath("_glob_helper.py")
        .read_bytes()
    )
