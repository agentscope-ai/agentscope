# -*- coding: utf-8 -*-
# pylint: disable=protected-access,consider-using-with
"""BubblewrapWorkspace -- sandboxed workspace backed by ``bwrap``."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import shlex
import shutil
import socket
import sys
import tempfile
from typing import Any

from ..._logging import logger
from ...mcp import MCPClient
from .._gateway_client import GatewayClient
from .._sandboxed_base import SandboxedWorkspaceBase
from .._utils import (
    _GATEWAY_BASE_REQUIREMENTS,
    _read_gateway_script_bytes,
    _read_glob_helper_bytes,
)
from ._bubblewrap_backend import BubblewrapBackend
from ._constants import (
    BWRAP_SMOKE_PROBE_ARGV,
    DEFAULT_GATEWAY_PORT,
    GATEWAY_HOME,
    SANDBOX_CACHE_DIR,
    SANDBOX_WORKDIR,
)

_DEFAULT_INSTRUCTIONS = """<workspace>
You have a Bubblewrap-based Linux workspace. All tool calls execute
inside the sandbox at ``{workdir}``.

Layout:

```
{workdir}/
|-- data/       # offloaded multimodal files
|-- skills/     # reusable skills
`-- sessions/   # session context and tool results
```
</workspace>"""


class BubblewrapWorkspace(SandboxedWorkspaceBase):
    """Workspace backed by Linux Bubblewrap.

    .. warning::
        This is **not** a network sandbox. The current TCP MCP gateway
        requires ``share_net=True`` so gateway requests from later ``bwrap``
        executions can reach the long-lived gateway process over host
        loopback. Sandboxed code therefore shares the host network namespace
        and can reach any service the host can — other loopback services,
        internal endpoints, and cloud metadata (e.g. ``169.254.169.254``).
        The gateway bearer token only guards the gateway itself, not the rest
        of the host network. Bubblewrap still isolates other namespaces and
        the mounted filesystem; only the network namespace is shared.

    ``host_workdir`` is mounted read-write at ``/workspace``. When it is
    omitted, an ephemeral temp directory is created and removed on close.
    Bootstrap caches are stored outside ``host_workdir`` by default, and the
    backend detects mount-source replacement before launching Bubblewrap. An
    explicit ``host_cache_dir`` must not overlap ``host_workdir`` or the
    backend's temporary directory; sharing one cache between mutually trusted
    workspaces is an opt-in tradeoff.
    """

    _gateway_home = GATEWAY_HOME
    _bootstrap_cmd_timeout = 1800.0

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        host_workdir: str | None = None,
        host_cache_dir: str | None = None,
        gateway_port: int | None = DEFAULT_GATEWAY_PORT,
        share_net: bool = True,
        env: dict[str, str] | None = None,
        extra_pip: list[str] | None = None,
        instructions: str = _DEFAULT_INSTRUCTIONS,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
    ) -> None:
        """Construct a :class:`BubblewrapWorkspace`.

        Args:
            workspace_id (`str | None`, optional):
                Stable workspace identifier.
            host_workdir (`str | None`, optional):
                Host directory mounted at ``/workspace``. Omit for an
                ephemeral workspace.
            host_cache_dir (`str | None`, optional):
                Host directory mounted at ``/tmp/.agentscope-cache`` for
                package caches. Omit to use a workspace-private cache outside
                ``host_workdir``. An explicit path must not overlap the
                workspace or its temporary directory. Supplying a shared
                directory weakens cross-workspace isolation and should only be
                used for mutually trusted workspaces.
            gateway_port (`int | None`, optional):
                TCP port used by the in-sandbox gateway. ``None`` allocates
                an available loopback port during initialization.
            share_net (`bool`, defaults to `True`):
                Must be ``True`` for this workspace because the TCP MCP
                gateway is reached across separate ``bwrap`` executions.
            env (`dict[str, str] | None`, optional):
                Extra environment variables for sandboxed commands.
            extra_pip (`list[str] | None`, optional):
                Extra Python requirements installed into the gateway venv.
                For a persistent ``host_workdir``, keep this list stable
                across workspace instances; changing it does not currently
                invalidate an existing Bootstrap environment.
            instructions (`str`, optional):
                System-prompt fragment template.
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs seeded on first initialization.
            skill_paths (`list[str] | None`, optional):
                Local skill directories seeded on first initialization.
        """
        self._validate_gateway_port(gateway_port)
        if not share_net:
            raise ValueError(
                "BubblewrapWorkspace currently requires share_net=True "
                "because its TCP MCP gateway must be reachable across "
                "separate bwrap executions.",
            )
        if host_workdir is not None and not host_workdir.strip():
            raise ValueError("host_workdir must not be empty.")
        if host_cache_dir is not None and not host_cache_dir.strip():
            raise ValueError("host_cache_dir must not be empty.")

        super().__init__(
            workspace_id=workspace_id,
            default_mcps=default_mcps,
            skill_paths=skill_paths,
        )

        self.workdir = SANDBOX_WORKDIR
        self.gateway_port = gateway_port
        self._gateway_port_input = gateway_port
        self._gateway_token = ""
        self._gateway_nonce = ""
        self._rotate_gateway_credentials()
        self.share_net = share_net
        self.env = dict(env or {})
        self.extra_pip = list(extra_pip or [])
        self.instructions = instructions

        self._host_workdir_input = host_workdir
        self._host_cache_dir_input = (
            os.path.abspath(host_cache_dir) if host_cache_dir else None
        )
        self._owned_workdir: tempfile.TemporaryDirectory[str] | None = None
        self._owned_cache_dir: tempfile.TemporaryDirectory[str] | None = None
        self.host_workdir = (
            os.path.abspath(host_workdir) if host_workdir else ""
        )
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._host_cache_dir = (
            os.path.abspath(host_cache_dir) if host_cache_dir else ""
        )
        self._backend: BubblewrapBackend | None = None
        self._gateway_process: asyncio.subprocess.Process | None = None

    @staticmethod
    def _validate_gateway_port(gateway_port: int | None) -> None:
        """Validate the configured TCP port before provisioning."""
        if gateway_port is None:
            return
        if (
            isinstance(gateway_port, bool)
            or not isinstance(gateway_port, int)
            or not 1 <= gateway_port <= 65535
        ):
            raise ValueError(
                "gateway_port must be None or an integer from 1 to 65535.",
            )

    @property
    def is_persistent(self) -> bool:
        """Whether the workspace files survive ``close``."""
        return self._host_workdir_input is not None

    async def _provision_backend(self) -> None:
        """Validate Bubblewrap availability and bind the backend."""
        if not sys.platform.startswith("linux"):
            raise RuntimeError("BubblewrapWorkspace requires Linux.")
        if shutil.which("bwrap") is None:
            raise RuntimeError("BubblewrapWorkspace requires 'bwrap' on PATH.")
        await self._probe_bubblewrap()
        if self._host_workdir_input is None:
            self._owned_workdir = tempfile.TemporaryDirectory()
            self.host_workdir = self._owned_workdir.name
        else:
            self.host_workdir = os.path.abspath(self._host_workdir_input)
        workdir_created = not os.path.lexists(self.host_workdir)
        os.makedirs(self.host_workdir, mode=0o700, exist_ok=True)
        if not os.path.isdir(self.host_workdir):
            raise ValueError("host_workdir must be a directory.")
        if workdir_created:
            os.chmod(self.host_workdir, 0o700)

        if (
            self._host_workdir_input is None
            and self._host_cache_dir_input is None
        ):
            self._owned_cache_dir = tempfile.TemporaryDirectory(
                prefix="agentscope-bwrap-cache-",
            )
            self._host_cache_dir = self._owned_cache_dir.name
        else:
            self._host_cache_dir = self._resolve_host_cache_dir()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._backend = BubblewrapBackend(
            host_workdir=self.host_workdir,
            host_tmpdir=self._tmpdir.name,
            host_cache_dir=self._host_cache_dir,
            workdir=SANDBOX_WORKDIR,
            share_net=self.share_net,
            env=self.env,
        )

    def _resolve_host_cache_dir(self) -> str:
        """Create and return a safe external cache directory."""
        workdir = os.path.realpath(self.host_workdir)
        if self._host_cache_dir_input is not None:
            path = os.path.abspath(self._host_cache_dir_input)
            make_private = False
        else:
            key = hashlib.blake2b(
                workdir.encode("utf-8"),
                digest_size=16,
            ).hexdigest()
            cache_root = os.path.join(
                os.path.dirname(workdir),
                ".agentscope-bwrap-cache",
            )
            self._ensure_cache_directory(
                cache_root,
                make_private=True,
            )
            path = os.path.join(
                cache_root,
                key,
            )
            make_private = True

        if os.path.lexists(path) and os.path.islink(path):
            raise ValueError("host_cache_dir must not be a symbolic link.")
        cache_dir = os.path.realpath(path)
        try:
            common = os.path.commonpath([workdir, cache_dir])
        except ValueError:
            common = ""
        if common in (workdir, cache_dir):
            raise ValueError(
                "host_cache_dir must not overlap host_workdir because it "
                "is used as a Bubblewrap bind source.",
            )

        self._ensure_cache_directory(
            path,
            make_private=make_private,
        )
        return os.path.realpath(path)

    @staticmethod
    def _ensure_cache_directory(
        path: str,
        *,
        make_private: bool,
    ) -> None:
        """Create a cache directory without accepting a symlink root."""
        if os.path.lexists(path) and os.path.islink(path):
            raise ValueError("host_cache_dir must not be a symbolic link.")
        created = not os.path.lexists(path)
        os.makedirs(path, mode=0o700, exist_ok=True)
        if os.path.islink(path) or not os.path.isdir(path):
            raise ValueError("host_cache_dir must be a real directory.")
        if created or make_private:
            os.chmod(path, 0o700)

    async def initialize(self) -> None:
        """Initialize and clean up partial resources on failure."""
        try:
            await super().initialize()
        except BaseException:
            try:
                await asyncio.shield(self.close())
            except BaseException:
                logger.exception(
                    "Bubblewrap cleanup after init failure failed",
                )
            raise

    async def _teardown_backend(self) -> None:
        """Terminate the gateway process and clean ephemeral directories."""
        try:
            await self._stop_gateway_process()
        except Exception as exc:
            logger.warning("Failed to stop Bubblewrap gateway: %s", exc)
        self._backend = None
        if self._tmpdir is not None:
            try:
                self._tmpdir.cleanup()
            except Exception as exc:
                logger.warning("Failed to clean Bubblewrap tmpdir: %s", exc)
            finally:
                self._tmpdir = None
        if self._owned_cache_dir is not None:
            try:
                self._owned_cache_dir.cleanup()
            except Exception as exc:
                logger.warning("Failed to clean Bubblewrap cache: %s", exc)
            finally:
                self._owned_cache_dir = None
                if self._host_cache_dir_input is None:
                    self._host_cache_dir = ""
        if self._owned_workdir is not None:
            try:
                self._owned_workdir.cleanup()
            except Exception as exc:
                logger.warning("Failed to clean Bubblewrap workdir: %s", exc)
            finally:
                self._owned_workdir = None
                if self._host_workdir_input is None:
                    self.host_workdir = ""

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment for this workspace."""
        return self.instructions.format(workdir=SANDBOX_WORKDIR)

    async def _setup_mcp_gateway(self) -> None:
        """Bootstrap and launch a tracked Bubblewrap gateway process."""
        backend = self.get_backend()

        if not await self._bootstrap_is_ready():
            logger.info(
                "BubblewrapWorkspace: bootstrapping or repairing "
                "workspace_id=%r",
                self.workspace_id,
            )
            for cmd in self._bootstrap_commands():
                result = await backend.exec_shell(
                    ["sh", "-c", cmd],
                    timeout=self._bootstrap_cmd_timeout,
                )
                if not result.ok():
                    raise RuntimeError(
                        "BubblewrapWorkspace bootstrap failed "
                        f"(exit {result.exit_code}) for: {cmd!r}\n"
                        f"stderr: {result.stderr.decode(errors='replace')}\n"
                        f"stdout: {result.stdout.decode(errors='replace')}",
                    )

        # These scripts are part of the installed AgentScope runtime rather
        # than user state, so refresh them even when a persistent workspace's
        # bootstrap artifacts are already usable.
        await backend.write_file(
            self._glob_helper_path,
            _read_glob_helper_bytes(),
        )
        await backend.write_file(
            self._gateway_script,
            _read_gateway_script_bytes(),
        )

        max_attempts = 3 if self._gateway_port_input is None else 1
        health_timeout = 30.0
        for attempt in range(max_attempts):
            if self._gateway_port_input is None:
                self.gateway_port = self._allocate_gateway_port()
            gateway_port = self.gateway_port
            assert gateway_port is not None
            await self._stop_gateway_process()
            self._rotate_gateway_credentials()

            launch_cmd = (
                f"exec {shlex.quote(self._gateway_python)} -I -u "
                f"{shlex.quote(self._gateway_script)} "
                f"--config {shlex.quote(self._mcp_file)} "
                f"--port {gateway_port} "
                f"--auth-token {shlex.quote(self._gateway_token)} "
                f"--instance-nonce {shlex.quote(self._gateway_nonce)} "
                f"> {shlex.quote(self._gateway_log)} 2>&1"
            )
            self._gateway_process = await backend.start_process(
                ["sh", "-c", launch_cmd],
                cwd=SANDBOX_WORKDIR,
            )

            self._gateway = GatewayClient(
                backend=backend,
                gateway_port=gateway_port,
                timeout=30.0,
                gateway_log_path=self._gateway_log,
                auth_token=self._gateway_token,
                instance_nonce=self._gateway_nonce,
            )
            if await self._wait_gateway_health(health_timeout):
                break
            if attempt == max_attempts - 1:
                await self._raise_gateway_timeout(health_timeout)
            await self._stop_gateway_process()
        else:  # pragma: no cover - loop always breaks or raises
            await self._raise_gateway_timeout(health_timeout)

        self._mcps = list(await self._gateway.list_mcps())

    async def _bootstrap_is_ready(self) -> bool:
        """Check whether persisted user-space bootstrap artifacts work."""
        result = await self.get_backend().exec_shell(
            [
                "sh",
                "-c",
                (
                    'test -f "$1" && '
                    'test -x "$2" && '
                    '"$2" --version >/dev/null 2>&1 && '
                    "\"$2\" -I -c 'import agentscope, fastapi, uvicorn, mcp' "
                    ">/dev/null 2>&1 && "
                    '"$3" --version >/dev/null 2>&1 && '
                    '"$4" --version >/dev/null 2>&1'
                ),
                "sh",
                self._gateway_script,
                self._gateway_python,
                f"{self._gateway_home}/bin/uv",
                f"{self._gateway_home}/bin/rg",
            ],
            timeout=30.0,
        )
        return result.ok()

    def _rotate_gateway_credentials(self) -> None:
        """Rotate per-launch gateway auth token and health nonce."""
        self._gateway_token = secrets.token_urlsafe(32)
        self._gateway_nonce = secrets.token_urlsafe(32)

    async def _wait_gateway_health(self, timeout: float) -> bool:
        """Wait for the tracked gateway process to answer health checks."""
        assert self._gateway is not None
        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.1
        while asyncio.get_event_loop().time() < deadline:
            if (
                self._gateway_process is not None
                and self._gateway_process.returncode is not None
            ):
                return False
            if await self._gateway.health():
                return True
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        return await self._gateway.health()

    @staticmethod
    def _allocate_gateway_port() -> int:
        """Return an available loopback TCP port for the gateway."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    async def _probe_bubblewrap() -> None:
        """Run a minimal Bubblewrap command to verify runtime support."""
        proc: asyncio.subprocess.Process | None = None
        kwargs: dict[str, Any] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            proc = await asyncio.create_subprocess_exec(
                *BWRAP_SMOKE_PROBE_ARGV,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=5.0,
            )
        except asyncio.TimeoutError as exc:
            if proc is not None:
                await BubblewrapBackend._terminate_process_tree(
                    proc,
                    grace=1.0,
                )
            raise RuntimeError(
                "BubblewrapWorkspace bwrap smoke probe timed out.",
            ) from exc
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise RuntimeError(
                "BubblewrapWorkspace requires Linux and a working "
                "'bwrap' executable.",
            ) from exc
        except BaseException:
            if proc is not None:
                await asyncio.shield(
                    BubblewrapBackend._terminate_process_tree(
                        proc,
                        grace=1.0,
                    ),
                )
            raise

        if proc.returncode != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace")
            raise RuntimeError(
                "BubblewrapWorkspace requires Linux and a working "
                f"'bwrap' executable. Smoke probe failed: {detail}",
            )

    def _bootstrap_commands(self) -> list[str]:
        """Return user-space provisioning commands for Bubblewrap."""
        pip_pkgs = [
            *_GATEWAY_BASE_REQUIREMENTS,
            *self.extra_pip,
        ]
        pip_args = " ".join(shlex.quote(p) for p in pip_pkgs)
        bin_dir = f"{self._gateway_home}/bin"
        uv_path = shlex.quote(f"{bin_dir}/uv")
        rg_path = shlex.quote(f"{bin_dir}/rg")
        python_path = shlex.quote(self._gateway_python)
        return [
            f"mkdir -p {shlex.quote(bin_dir)}",
            f"if ! {uv_path} --version >/dev/null 2>&1; then "
            f"rm -f {uv_path}; "
            f"{self._install_uv_script(bin_dir)}; "
            "fi",
            f"if ! {python_path} --version >/dev/null 2>&1; then "
            f"rm -rf {shlex.quote(self._gateway_venv)}; "
            f"{uv_path} venv {shlex.quote(self._gateway_venv)}; "
            "fi",
            f"{uv_path} pip install --python {python_path} " f"{pip_args}",
            f"if ! {rg_path} --version >/dev/null 2>&1; then "
            f"rm -f {rg_path}; "
            f"{self._install_ripgrep_script()}; "
            "fi",
            f"{rg_path} --version",
            f"{uv_path} pip install --python {python_path} "
            "--no-deps 'agentscope'",
        ]

    @staticmethod
    def _install_uv_script(bin_dir: str) -> str:
        """Return shell code that downloads and executes the uv installer."""
        quoted_bin_dir = shlex.quote(bin_dir)
        return (
            "set -eu; "
            "tmp_installer=$(mktemp); "
            'cleanup_installer() { rm -f "$tmp_installer"; }; '
            "trap cleanup_installer EXIT INT TERM; "
            "curl -LsSf --retry 5 --retry-delay 2 --retry-all-errors "
            "--connect-timeout 15 --max-time 60 --retry-max-time 180 "
            '-o "$tmp_installer" '
            "https://astral.sh/uv/install.sh; "
            f"env UV_INSTALL_DIR={quoted_bin_dir} "
            'INSTALLER_NO_MODIFY_PATH=1 sh "$tmp_installer"; '
            'rm -f "$tmp_installer"; '
            "trap - EXIT INT TERM"
        )

    def _install_ripgrep_script(self) -> str:
        """Return shell code that installs official ripgrep release assets."""
        bin_dir = shlex.quote(f"{self._gateway_home}/bin")
        cache_dir = shlex.quote(f"{SANDBOX_CACHE_DIR}/ripgrep")
        version = "14.1.0"
        base_url = (
            "https://github.com/BurntSushi/ripgrep/releases/download/"
            f"{version}"
        )
        curl_release_asset = (
            "curl -fL --retry 5 --retry-delay 2 --retry-all-errors "
            "--connect-timeout 15 --max-time 90 --retry-max-time 180"
        )
        x86_64_sha256 = (
            "f84757b07f425fe5cf11d87df6644691"
            "c644a5cd2348a2c670894272999d3ba7"
        )
        aarch64_sha256 = (
            "c8c210b99844fbf16b7a36d1c963e835"
            "1bca5ff2dd7c788f5fba4ac18ba8c60d"
        )
        return (
            "set -eu; "
            "arch=$(uname -m); "
            'case "$arch" in '
            "x86_64|amd64) "
            f"asset=ripgrep-{version}-x86_64-unknown-linux-musl.tar.gz; "
            f"sha256={x86_64_sha256} ;; "
            "aarch64|arm64) "
            f"asset=ripgrep-{version}-aarch64-unknown-linux-gnu.tar.gz; "
            f"sha256={aarch64_sha256} ;; "
            "*) echo "
            '"unsupported ripgrep architecture: $arch" >&2; exit 1 ;; '
            "esac; "
            f"mkdir -p {cache_dir} {bin_dir}; "
            f"cd {cache_dir}; "
            'if ! printf "%s  %s\\n" "$sha256" "$asset" '
            "| sha256sum -c - >/dev/null 2>&1; then "
            'tmp_asset=$(mktemp "${asset}.tmp.XXXXXX"); '
            'cleanup_asset() { rm -f "$tmp_asset"; }; '
            "trap cleanup_asset EXIT INT TERM; "
            f'{curl_release_asset} -o "$tmp_asset" '
            f'{shlex.quote(base_url)}/"$asset"; '
            'printf "%s  %s\\n" "$sha256" "$tmp_asset" '
            "| sha256sum -c -; "
            'mv -f "$tmp_asset" "$asset"; '
            "trap - EXIT INT TERM; "
            "fi; "
            "tmp=$(mktemp -d); "
            'tar -xzf "$asset" -C "$tmp"; '
            f"tmp_rg=$(mktemp {bin_dir}/.rg.tmp.XXXXXX); "
            'cleanup_rg() { rm -f "$tmp_rg"; }; '
            "trap cleanup_rg EXIT INT TERM; "
            'cp "$tmp"/ripgrep-*/rg "$tmp_rg"; '
            'chmod +x "$tmp_rg"; '
            f'mv -f "$tmp_rg" {bin_dir}/rg; '
            "trap - EXIT INT TERM; "
            'rm -rf "$tmp"'
        )

    async def _stop_gateway_process(self) -> None:
        """Terminate any tracked gateway process."""
        proc = self._gateway_process
        if proc is None:
            return
        try:
            if proc.returncode is None:
                await BubblewrapBackend._terminate_process_tree(
                    proc,
                    grace=5.0,
                )
        finally:
            if proc.returncode is not None:
                self._gateway_process = None

    async def _raise_gateway_timeout(self, timeout: float) -> None:
        """Raise a gateway-start failure with the log tail included."""
        try:
            log = await self.get_backend().read_file(self._gateway_log)
            tail = log[-2000:].decode(errors="replace")
        except Exception:
            tail = "<no gateway log available>"
        code = (
            self._gateway_process.returncode
            if self._gateway_process is not None
            else None
        )
        raise RuntimeError(
            f"gateway did not become healthy within {timeout}s "
            f"(process returncode={code}). Tail of {self._gateway_log}:\n"
            f"{tail}",
        )
