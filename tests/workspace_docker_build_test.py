# -*- coding: utf-8 -*-
"""Tests for the Docker build context, which needs no Docker daemon."""
import shutil
import unittest

from agentscope.workspace._docker._make_dockerfile import (
    prepare_build_context,
)


class DockerGatewayInstallTest(unittest.TestCase):
    """The gateway image installs agentscope without its dependencies."""

    def test_pip_install_lines(self) -> None:
        """Only requirements.txt is installed with dependencies."""
        ctx_dir, _, _ = prepare_build_context()
        dockerfile = (ctx_dir / "Dockerfile").read_text(encoding="utf-8")
        shutil.rmtree(ctx_dir, ignore_errors=True)
        self.assertListEqual(
            [
                line
                for line in dockerfile.splitlines()
                if line.startswith("RUN uv pip install")
            ],
            [
                "RUN uv pip install -r /root/.agentscope/requirements.txt",
                'RUN uv pip install --no-deps "agentscope"',
            ],
        )
