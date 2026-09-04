# -*- coding: utf-8 -*-
"""Launch and probe the packaged AgentScope Desktop backend."""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

AUTH_TOKEN_ENV = "AGENTSCOPE_DESKTOP_TOKEN"
BACKEND_PORT_PREFIX = "AGENTSCOPE_BACKEND_PORT="
DESKTOP_SHUTDOWN_COMMAND = "AGENTSCOPE_DESKTOP_SHUTDOWN"
STARTUP_TIMEOUT_SECONDS = 30


def backend_executable() -> Path:
    """Return the current platform's PyInstaller backend executable."""
    executable_name = (
        "agentscope-backend.exe"
        if sys.platform == "win32"
        else "agentscope-backend"
    )
    return (
        Path(__file__).resolve().parent
        / "dist"
        / "agentscope-backend"
        / executable_name
    )


def bundled_ripgrep(executable: Path) -> Path:
    """Return the ripgrep binary bundled in the PyInstaller onedir."""
    executable_name = "rg.exe" if sys.platform == "win32" else "rg"
    return executable.parent / "_internal" / executable_name


def read_backend_port(
    process: subprocess.Popen[str],
    timeout: int,
) -> int:
    """Read the bounded port handshake without blocking indefinitely."""
    lines: queue.Queue[str | None] = queue.Queue()

    def collect_stdout() -> None:
        if process.stdout is None:
            lines.put(None)
            return
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=collect_stdout, daemon=True).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(deadline - time.monotonic(), 0.01)
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty as error:
            raise RuntimeError("Backend port handshake timed out.") from error
        if line is None:
            break
        stripped = line.strip()
        if stripped.startswith(BACKEND_PORT_PREFIX):
            return int(stripped.removeprefix(BACKEND_PORT_PREFIX))
    stderr = process.stderr.read().strip() if process.stderr else ""
    detail = f"\n{stderr}" if stderr else ""
    raise RuntimeError(
        f"Backend exited before its port handshake: {process.poll()}"
        f"{detail}",
    )


def wait_for_health(
    process: subprocess.Popen[str],
    port: int,
    token: str,
    timeout: int,
) -> dict:
    """Wait for authenticated packaged-backend readiness."""
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read().strip() if process.stderr else ""
            detail = f"\n{stderr}" if stderr else ""
            raise RuntimeError(
                f"Backend exited before becoming healthy: "
                f"{process.returncode}{detail}",
            )
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                return json.load(response)
        except (OSError, urllib.error.URLError, TimeoutError):
            time.sleep(0.25)
    raise RuntimeError("Packaged backend health check timed out.")


def request_json(
    port: int,
    token: str,
    method: str,
    path: str,
    payload: dict | None = None,
) -> dict:
    """Send an authenticated JSON request to the packaged backend."""
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(
        request,
        timeout=STARTUP_TIMEOUT_SECONDS,
    ) as response:
        return json.load(response)


def create_knowledge_base(
    port: int,
    token: str,
    credential_id: str,
    name: str,
) -> str:
    """Create a credential and persistent Qdrant collection."""
    credential = request_json(
        port,
        token,
        "POST",
        "/credential/",
        {
            "data": {
                "type": "openai_credential",
                "id": credential_id,
                "name": "Desktop packaging smoke",
                "api_key": "not-a-real-api-key",
            },
        },
    )
    assert credential == {"credential_id": credential_id}, credential
    created = request_json(
        port,
        token,
        "POST",
        "/knowledge_bases/",
        {
            "name": name,
            "description": "Packaged desktop persistence smoke test",
            "embedding_model_config": {
                "type": "openai_credential",
                "credential_id": credential_id,
                "model": "text-embedding-3-small",
                "dimensions": 3,
                "parameters": {},
            },
        },
    )
    knowledge_base_id = created["knowledge_base_id"]
    assert created == {"knowledge_base_id": knowledge_base_id}, created
    return knowledge_base_id


def stop_backend(process: subprocess.Popen[str]) -> None:
    """Request graceful shutdown before using a forced fallback."""
    if process.poll() is not None:
        return
    try:
        if process.stdin:
            process.stdin.write(f"{DESKTOP_SHUTDOWN_COMMAND}\n")
            process.stdin.flush()
    except BrokenPipeError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def probe_launch(
    executable: Path,
    data_dir: str,
    token: str,
    expected_ids: list[str],
    credential_id: str,
    knowledge_base_name: str,
) -> str:
    """Probe one packaged launch and create a knowledge base."""
    environment = {**os.environ, AUTH_TOKEN_ENV: token}
    with subprocess.Popen(
        [str(executable), "--port", "0", "--data-dir", data_dir],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            port = read_backend_port(process, STARTUP_TIMEOUT_SECONDS)
            health = wait_for_health(
                process,
                port,
                token,
                STARTUP_TIMEOUT_SECONDS,
            )
            assert health["status"] == "ok", health
            assert health["components"]["storage"] == "ok", health
            assert health["components"]["knowledge_base"] == "ok", health

            listed = request_json(
                port,
                token,
                "GET",
                "/knowledge_bases/",
            )
            listed_ids = {item["id"] for item in listed["knowledge_bases"]}
            assert listed_ids == set(expected_ids), listed

            knowledge_base_id = create_knowledge_base(
                port,
                token,
                credential_id,
                knowledge_base_name,
            )
            assert (Path(data_dir) / "agentscope.db").is_file()
            return knowledge_base_id
        finally:
            stop_backend(process)


def main() -> None:
    """Assert packaged startup and knowledge-base persistence."""
    executable = backend_executable()
    if not executable.is_file():
        raise FileNotFoundError(f"Packaged backend not found: {executable}")
    ripgrep = bundled_ripgrep(executable)
    if not ripgrep.is_file():
        raise FileNotFoundError(f"Bundled ripgrep not found: {ripgrep}")
    ripgrep_version = subprocess.run(
        [str(ripgrep), "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert ripgrep_version.stdout.startswith("ripgrep "), ripgrep_version

    token = "desktop-packaging-smoke-token"
    with tempfile.TemporaryDirectory() as data_dir:
        first_id = probe_launch(
            executable,
            data_dir,
            token,
            [],
            "desktop-packaging-smoke-credential-1",
            "First packaged KB",
        )
        first_collection = (
            Path(data_dir)
            / "qdrant"
            / "collection"
            / f"kb_{first_id}"
            / "storage.sqlite"
        )
        assert first_collection.is_file()

        second_id = probe_launch(
            executable,
            data_dir,
            token,
            [first_id],
            "desktop-packaging-smoke-credential-2",
            "Second packaged KB",
        )
        assert second_id != first_id
        second_collection = (
            Path(data_dir)
            / "qdrant"
            / "collection"
            / f"kb_{second_id}"
            / "storage.sqlite"
        )
        assert second_collection.is_file()


if __name__ == "__main__":
    main()
