# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for the Docker build context, which needs no Docker daemon."""
import shutil
import unittest
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.workspace._utils import UV_VERSION
from agentscope.workspace._docker._docker_workspace import DockerWorkspace

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

    def test_certificate_requirements(self) -> None:
        """Update gateway certificates without modifying system Python."""
        ctx_dir, _, copy_files = prepare_build_context()
        try:
            self.assertEqual(
                sorted(copy_files),
                ["_glob_helper.py", "_mcp_gateway_app.py", "requirements.txt"],
            )
            requirements = (ctx_dir / "requirements.txt").read_text()
            dockerfile = (ctx_dir / "Dockerfile").read_text()
            self.assertIn("certifi>=2026.10.19\n", requirements)
            self.assertIn(
                f"https://astral.sh/uv/{UV_VERSION}/install.sh",
                dockerfile,
            )
            self.assertNotIn("uv pip install --system", dockerfile)
            self.assertNotIn("--break-system-packages", dockerfile)
            self.assertIn("update-ca-certificates", dockerfile)
            self.assertNotIn("| env", dockerfile)
        finally:
            shutil.rmtree(ctx_dir)

    def test_uv_version_changes_image_tag(self) -> None:
        """Changing the installer pin invalidates the generated image tag."""
        ctx_dir, original_tag, _ = prepare_build_context()
        self.addCleanup(shutil.rmtree, ctx_dir)
        with patch(
            "agentscope.workspace._docker._make_dockerfile.UV_VERSION",
            "0.13.5",
        ):
            updated_dir, updated_tag, _ = prepare_build_context()
        self.addCleanup(shutil.rmtree, updated_dir)
        self.assertNotEqual(original_tag, updated_tag)
        self.assertIn(
            "https://astral.sh/uv/0.13.5/install.sh",
            (updated_dir / "Dockerfile").read_text(),
        )


class DockerBuildCompatibilityTest(unittest.IsolatedAsyncioTestCase):
    """Builds preserve local image support and normal layer caching."""

    async def test_local_base_does_not_force_pull_or_disable_cache(
        self,
    ) -> None:
        """A local-only base must not require access to a registry."""
        workspace = DockerWorkspace(
            workspace_id="local-build",
            base_image="local-only:custom",
        )
        workspace._client = MagicMock()
        workspace._client.images.inspect = AsyncMock(side_effect=ValueError)

        async def stream() -> AsyncIterator[dict[str, str]]:
            """Yield one successful Docker build event."""
            yield {"stream": "built"}

        workspace._client.images.build.return_value = stream()
        await workspace._build_or_reuse_image()
        options = workspace._client.images.build.call_args.kwargs
        self.assertNotIn("pull", options)
        self.assertNotIn("nocache", options)

    async def test_current_image_can_be_reused(self) -> None:
        """The changed template yields a new tag; safe cache hits stay fast."""
        workspace = DockerWorkspace(workspace_id="tls-cache")
        workspace._client = MagicMock()
        workspace._client.images.inspect = AsyncMock()
        await workspace._build_or_reuse_image()
        workspace._client.images.build.assert_not_called()
