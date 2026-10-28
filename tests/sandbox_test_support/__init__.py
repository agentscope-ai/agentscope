# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Install the current checkout only in opted-in live workspace tests."""

import atexit
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack, suppress
from functools import cache
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase, SkipTest
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from agentscope.workspace import BubblewrapWorkspace, DockerWorkspace
from agentscope.workspace._docker import _docker_workspace as docker_module
from agentscope.workspace._docker._make_dockerfile import compute_image_tag
from agentscope.workspace._utils import UV_VERSION

_VERIFY_SCRIPT = Path(__file__).with_name("_verify_wheel.py")
_WHEEL_BUILD_ERROR: Exception | None = None


@cache
def checkout_wheel() -> Path:
    """Build once per process without leaving build artifacts in the repo."""
    global _WHEEL_BUILD_ERROR
    if _WHEEL_BUILD_ERROR is not None:
        raise _WHEEL_BUILD_ERROR
    try:
        return _build_checkout_wheel()
    except Exception as exc:
        _WHEEL_BUILD_ERROR = exc
        raise


def _build_checkout_wheel() -> Path:
    """Build the checkout with the host toolchain and retain the artifact."""
    uv = shutil.which("uv")
    if uv is None:
        raise SkipTest(
            "Checkout wheel tests require uv on the host PATH. "
            f'Install it with: python -m pip install "uv>={UV_VERSION}"',
        )
    # Use the sandbox CA baseline: uv fetches build backends with bundled CAs.
    try:
        output = subprocess.check_output(
            [uv, "--version"],
            text=True,
            timeout=10,
        ).strip()
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise RuntimeError(
            "Could not check host uv version. Check or upgrade uv on the "
            f"host PATH (requires uv>={UV_VERSION}): {exc}",
        ) from exc
    version = re.match(r"uv (\d+)\.(\d+)\.(\d+)(?:\s|$)", output)
    if version is None or tuple(map(int, version.groups())) < tuple(
        map(int, UV_VERSION.split(".")),
    ):
        raise RuntimeError(
            f"Checkout wheel tests require uv>={UV_VERSION}; "
            f"found {output!r}. Upgrade uv on the host PATH.",
        )
    repo = Path(__file__).resolve().parents[2]
    # Keep the wheel alive across test classes until the process exits.
    # pylint: disable=consider-using-with
    temporary = tempfile.TemporaryDirectory(prefix="as-test-wheel-")
    root = Path(temporary.name)
    source = root / "source"
    source.mkdir()
    try:
        # Keep these root inputs in sync with the packaging configuration.
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(repo / name, source / name)
        # Include uncommitted and untracked source files in the working tree.
        shutil.copytree(
            repo / "src",
            source / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
        )
        subprocess.run(
            [
                uv,
                "build",
                "--wheel",
                "--python",
                sys.executable,
                "--out-dir",
                str(root / "wheels"),
                str(source),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
            # Fix ZIP timestamps so unchanged wheels reuse Docker image tags.
            env={**os.environ, "SOURCE_DATE_EPOCH": "315532800"},
        )
        wheels = list((root / "wheels").glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"Expected one checkout wheel, got {wheels}")
        with ZipFile(wheels[0]) as archive:
            licenses = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/licenses/LICENSE")
            ]
            if (
                len(licenses) != 1
                or archive.read(licenses[0])
                != (source / "LICENSE").read_bytes()
            ):
                raise RuntimeError(
                    "Checkout wheel must contain the complete LICENSE",
                )
    except subprocess.TimeoutExpired as exc:
        temporary.cleanup()
        raise RuntimeError(
            f"Checkout wheel build timed out after {exc.timeout} seconds",
        ) from exc
    except subprocess.CalledProcessError as exc:
        temporary.cleanup()
        raise RuntimeError(
            f"Checkout wheel build failed:\n{exc.stdout}\n{exc.stderr}",
        ) from exc
    except BaseException:
        temporary.cleanup()
        raise
    atexit.register(temporary.cleanup)
    return wheels[0]


def wheel_build_context(
    prepare: Any,
    wheel: Path,
    **kwargs: Any,
) -> tuple[Path, str, dict[str, bytes]]:
    """Replace the index install and include wheel bytes in the image tag."""
    context, _, payloads = prepare(**kwargs)
    try:
        dockerfile = (context / "Dockerfile").read_text(encoding="utf-8")
        original = 'RUN uv pip install --no-deps "agentscope"'
        if dockerfile.count(original) != 1:
            raise RuntimeError("Expected one Docker AgentScope install")
        payloads[wheel.name] = wheel.read_bytes()
        payloads[_VERIFY_SCRIPT.name] = _VERIFY_SCRIPT.read_bytes()
        install = (
            f"COPY {wheel.name} /tmp/{wheel.name}\n"
            f"COPY {_VERIFY_SCRIPT.name} /tmp/{_VERIFY_SCRIPT.name}\n"
            f"RUN uv pip install --no-deps --reinstall /tmp/{wheel.name}"
            f" && python -I /tmp/{_VERIFY_SCRIPT.name} /tmp/{wheel.name}"
            f" && rm /tmp/{wheel.name} /tmp/{_VERIFY_SCRIPT.name}"
        )
        dockerfile = dockerfile.replace(original, install)
        (context / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        for name, data in payloads.items():
            (context / name).write_bytes(data)
        return context, compute_image_tag(dockerfile, payloads), payloads
    except BaseException:
        shutil.rmtree(context)
        raise


async def setup_checkout_gateway(
    workspace: Any,
    wheel: Path,
    setup_gateway: Any,
) -> None:
    """Install and verify before delegating to the normal gateway launch."""
    backend = workspace.get_backend()
    upload_dir = f"{workspace.workdir}/.checkout-wheel-{uuid4().hex}"
    wheel_path = f"{upload_dir}/{wheel.name}"
    verify_path = f"{upload_dir}/{_VERIFY_SCRIPT.name}"
    commands = workspace._bootstrap_commands()
    installs = [
        index
        for index, command in enumerate(commands)
        if shlex.split(command)[-1:] == ["agentscope"]
    ]
    if len(installs) != 1:
        raise RuntimeError("Expected one bootstrap AgentScope install")
    index = installs[0]
    install_args = shlex.split(commands[index])
    install_args[-1:] = ["--reinstall", wheel_path]
    verify = (
        f"{shlex.quote(workspace._gateway_python)} -I "
        f"{shlex.quote(verify_path)} {shlex.quote(wheel_path)}"
    )
    commands[index] = f"{shlex.join(install_args)} && {verify}"
    if isinstance(workspace, BubblewrapWorkspace):
        ready = await workspace._bootstrap_is_ready()
    else:
        ready = await backend.file_exists(workspace._gateway_script)
    if ready:
        # Preserve resume failures instead of repairing a damaged package.
        commands = [verify]
    result = await backend.exec_shell(["mkdir", upload_dir])
    if not result.ok():
        raise RuntimeError(
            f"Could not create checkout upload dir: {upload_dir}",
        )
    try:
        await backend.write_file(wheel_path, wheel.read_bytes())
        await backend.write_file(verify_path, _VERIFY_SCRIPT.read_bytes())
        for command in commands:
            result = await backend.exec_shell(
                ["sh", "-c", command],
                timeout=workspace._bootstrap_cmd_timeout,
            )
            if not result.ok():
                raise RuntimeError(
                    f"Checkout bootstrap failed for {command!r}: "
                    f"{result.exit_code}\n"
                    f"{result.stdout.decode(errors='replace')}\n"
                    f"{result.stderr.decode(errors='replace')}",
                )
    except BaseException:
        # Cleanup is best effort here so the original failure stays visible.
        with suppress(Exception):
            await backend.exec_shell(["rm", "-rf", upload_dir])
        raise
    result = await backend.exec_shell(["rm", "-rf", upload_dir])
    if not result.ok():
        raise RuntimeError("Could not remove uploaded checkout artifacts")
    # Dependencies and wheel are ready; retain normal script upload and launch.
    with patch.object(workspace, "_bootstrap_commands", return_value=[]):
        await setup_gateway(workspace)


class CheckoutWheelTestCase(IsolatedAsyncioTestCase):
    """Scope checkout installation patches to a live integration test class."""

    workspace_type: type

    @classmethod
    def setUpClass(cls) -> None:
        """Skipped classes never build; all enabled classes share one wheel."""
        super().setUpClass()
        if getattr(cls, "workspace_type", None) is None:
            raise TypeError(
                f"{cls.__name__} must set workspace_type "
                f"to the workspace class under test",
            )
        wheel = checkout_wheel()
        stack = ExitStack()
        cls.addClassCleanup(stack.close)
        if issubclass(cls.workspace_type, DockerWorkspace):
            prepare = docker_module.prepare_build_context

            def prepare_checkout(**kwargs: Any) -> Any:
                """Adapt only live Docker builds."""
                return wheel_build_context(prepare, wheel, **kwargs)

            stack.enter_context(
                patch.object(
                    docker_module,
                    "prepare_build_context",
                    prepare_checkout,
                ),
            )
        else:
            original = cls.workspace_type._setup_mcp_gateway

            async def setup(workspace: Any) -> None:
                """Install the wheel before the production gateway starts."""
                await setup_checkout_gateway(workspace, wheel, original)

            stack.enter_context(
                patch.object(cls.workspace_type, "_setup_mcp_gateway", setup),
            )
