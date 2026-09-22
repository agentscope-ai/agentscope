# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for the Docker build context, which needs no Docker daemon.

:func:`prepare_build_context` only writes files into a temporary
directory, so these cases run on every platform — unlike
``workspace_docker_test.py``, whose classes are skipped wholesale when
no daemon is reachable.
"""

import shutil
import unittest

from agentscope.workspace._docker._make_dockerfile import (
    GATEWAY_HOME,
    prepare_build_context,
)
from agentscope.workspace._utils import _GATEWAY_BASE_REQUIREMENTS


def _pip_instructions(dockerfile_text: str) -> list[str]:
    """Return every ``uv pip install`` instruction, in build order.

    Args:
        dockerfile_text: Rendered Dockerfile content.

    Returns:
        One whitespace-normalised string per ``uv pip install``
        instruction, with line continuations folded. Comment lines that
        merely mention the command are not instructions.
    """
    folded = dockerfile_text.replace("\\\n", " ")
    return [
        " ".join(line.split())
        for line in folded.splitlines()
        if line.strip().startswith("RUN") and "uv pip install" in line
    ]


class DockerGatewayInstallTest(unittest.TestCase):
    """The gateway venv must stay as small as the gateway's imports."""

    def _render(self, **kwargs: object) -> tuple[list[str], list[str]]:
        """Build a context and return its pip instructions and pins.

        Args:
            **kwargs: Forwarded to :func:`prepare_build_context`.

        Returns:
            ``(pip_instructions, requirements_lines)`` for the rendered
            build context.
        """
        ctx_dir, _, copy_files = prepare_build_context(**kwargs)
        try:
            dockerfile = (ctx_dir / "Dockerfile").read_text(
                encoding="utf-8",
            )
        finally:
            shutil.rmtree(ctx_dir, ignore_errors=True)
        requirements = copy_files["requirements.txt"].decode("utf-8")
        return _pip_instructions(dockerfile), requirements.splitlines()

    def test_agentscope_installs_without_its_dependency_tree(
        self,
    ) -> None:
        """Only the gateway pins are installed with their deps."""
        installs, _ = self._render()
        self.assertEqual(
            installs,
            [
                f"RUN uv pip install -r {GATEWAY_HOME}/requirements.txt",
                'RUN uv pip install --no-deps "agentscope"',
            ],
        )

    def test_gateway_pins_are_installed_with_their_own_deps(self) -> None:
        """``--no-deps`` applies to agentscope, never to requirements.

        The base requirements are the gateway's whole runtime surface;
        installing them without deps would leave the venv unable to
        start the script.
        """
        installs, requirements = self._render(
            extra_pip=["extra-a", "extra-b"],
        )
        for package in _GATEWAY_BASE_REQUIREMENTS:
            self.assertIn(package, requirements)
        for package in ("extra-a", "extra-b"):
            self.assertIn(package, requirements)
        pinned = [i for i in installs if "requirements.txt" in i]
        self.assertEqual(len(pinned), 1)
        self.assertNotIn("--no-deps", pinned[0])


if __name__ == "__main__":
    unittest.main()
