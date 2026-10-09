# -*- coding: utf-8 -*-
"""Prepared-template build failures, identity validation, and cleanup."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless
from unittest.mock import AsyncMock, Mock, patch

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "opensandbox_template_builder",
    REPO / "examples/workspace/opensandbox/build_template.py",
)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
DIGEST = "sha256:" + "a" * 64

try:
    from opensandbox import Sandbox, SandboxManager
    from opensandbox.models.templates import CreateTemplateRequest

    HAS_TEMPLATE_SDK = hasattr(Sandbox, "create_from_template")
except ImportError:
    HAS_TEMPLATE_SDK = False


def options(output: Path, local: bool = False) -> SimpleNamespace:
    """Use a tagged registry with a port to exercise digest qualification."""
    return SimpleNamespace(
        image="localhost:5000/agentscope:version",
        publish="s3://templates/publish",
        local=local,
        domain="server:80",
        protocol="http",
        cpu=1,
        memory="2Gi",
        disk="4Gi",
        nameservers=["100.100.2.136"],
        requirements=None,
        wait_timeout=1,
        output=output,
    )


def template(phase: str) -> SimpleNamespace:
    """Return only lifecycle fields needed by the builder."""
    return SimpleNamespace(
        template_id="tpl-test",
        status=SimpleNamespace(phase=phase, message="build diagnostic"),
    )


class TestBuildImage(TestCase):
    """Build contexts and Docker manifest identities must be deterministic."""

    def test_isolated_wheel_context_and_requirements(self) -> None:
        """Ignore stale recipe wheels and bake the supplied requirements."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = root / "recipe"
            recipe.mkdir()
            (recipe / "requirements.txt").write_text("")
            (recipe / "agentscope-stale.whl").touch()
            extra = root / "extra.txt"
            extra.write_text("requests==2.32.5\n")
            args = options(root / "result.json")
            args.requirements = extra

            def run(command: list[str]) -> None:
                if command[0] == "uv":
                    wheels = Path(command[command.index("--out-dir") + 1])
                    wheels.mkdir()
                    (wheels / "agentscope-current.whl").touch()
                else:
                    context = Path(command[-1])
                    self.assertEqual(
                        [p.name for p in context.glob("*.whl")],
                        ["agentscope-current.whl"],
                    )
                    self.assertEqual(
                        (context / "requirements.txt").read_text(),
                        extra.read_text(),
                    )
                    self.assertIn("--push", command)
                    self.assertIn("linux/amd64", command)
                    metadata = Path(
                        command[command.index("--metadata-file") + 1],
                    )
                    metadata.write_text(
                        json.dumps({"containerimage.digest": DIGEST}),
                    )

            with (
                patch.object(builder, "RECIPE", recipe),
                patch.object(builder, "run_command", side_effect=run),
            ):
                image = builder.build_image(args)
            self.assertEqual(image, "localhost:5000/agentscope@" + DIGEST)

    def test_multiple_wheels_stop_before_docker(self) -> None:
        """Do not silently select one version from an ambiguous wheel build."""

        def run(command: list[str]) -> None:
            wheels = Path(command[command.index("--out-dir") + 1])
            wheels.mkdir()
            for name in ("agentscope-a.whl", "agentscope-b.whl"):
                (wheels / name).touch()

        with patch.object(builder, "run_command", side_effect=run) as command:
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                builder.build_image(options(Path("unused.json")))
        self.assertEqual(command.call_count, 1)

    def test_dry_run_makes_no_changes_or_leaks_credentials(self) -> None:
        """Dry-run makes no build/API calls and does not serialize keys."""
        stdout = io.StringIO()
        with (
            patch.dict("os.environ", {"OPENSANDBOX_API_KEY": "test-secret"}),
            patch.object(
                builder, "build_template", new_callable=AsyncMock
            ) as build,
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(
                builder.main(
                    ["--image", "image:local", "--local", "--dry-run"]
                ),
                0,
            )
        build.assert_not_awaited()
        self.assertNotIn("test-secret", stdout.getvalue())
        self.assertEqual(
            json.loads(stdout.getvalue())["platform"], "linux/amd64"
        )

    def test_local_failed_health_check_removes_container(self) -> None:
        """Remove the local container when readiness validation fails."""
        with (
            patch.object(builder, "run_command") as command,
            patch.object(
                builder.subprocess, "run", return_value=Mock(returncode=1)
            ),
            patch.object(builder.time, "monotonic", side_effect=[0, 1]),
        ):
            with self.assertRaisesRegex(RuntimeError, "readiness"):
                builder.validate_local("image:local", 1)
        self.assertEqual(
            command.call_args.args[0][:4],
            ["docker", "rm", "--force", "--volumes"],
        )


class TestLocalBuild(IsolatedAsyncioTestCase):
    """Local mode must work without contacting a lifecycle service."""

    async def test_local_does_not_import_sdk(self) -> None:
        """Load and validate locally even when the optional SDK is absent."""
        with TemporaryDirectory() as directory:
            args = options(Path(directory) / "result.json", local=True)
            with (
                patch.dict("sys.modules", {"opensandbox": None}),
                patch.object(builder.shutil, "which", return_value="tool"),
                patch.object(builder, "run_command"),
                patch.object(builder, "build_image", return_value=args.image),
                patch.object(builder, "validate_local") as validate,
            ):
                result = await builder.build_template(args)
            validate.assert_called_once_with(args.image, args.wait_timeout)
            self.assertTrue(result["validated"])
            self.assertNotIn("template_id", result)


@skipUnless(HAS_TEMPLATE_SDK, "install a template-capable OpenSandbox SDK")
class TestRemoteBuild(IsolatedAsyncioTestCase):
    """Failures must preserve IDs and avoid false success exports."""

    async def test_failed_template_retains_id_and_closes_manager(self) -> None:
        """Retain the accepted template so a failed build can be inspected."""
        with TemporaryDirectory() as directory:
            args = options(Path(directory) / "result.json")
            manager = AsyncMock()
            manager.create_template.return_value = template("Failed")
            with (
                patch.object(SandboxManager, "create", return_value=manager),
                patch.object(builder.shutil, "which", return_value="tool"),
                patch.object(builder, "run_command"),
                patch.object(
                    builder, "build_image", return_value="image@" + DIGEST
                ),
                patch.object(
                    builder, "validate_template", new_callable=AsyncMock
                ) as validate,
            ):
                with self.assertRaisesRegex(RuntimeError, "build diagnostic"):
                    await builder.build_template(args)
            saved = json.loads(args.output.read_text())
            self.assertEqual(saved["template_id"], "tpl-test")
            self.assertEqual(saved["phase"], "Failed")
            self.assertFalse(saved["validated"])
            validate.assert_not_awaited()
            manager.close.assert_awaited_once()

    async def test_build_failure_never_submits_template(self) -> None:
        """Do not submit a template when Docker fails to push its image."""
        with TemporaryDirectory() as directory:
            args = options(Path(directory) / "result.json")
            manager = AsyncMock()
            with (
                patch.object(SandboxManager, "create", return_value=manager),
                patch.object(builder.shutil, "which", return_value="tool"),
                patch.object(builder, "run_command"),
                patch.object(
                    builder,
                    "build_image",
                    side_effect=RuntimeError("push failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "push failed"):
                    await builder.build_template(args)
            manager.create_template.assert_not_awaited()
            manager.close.assert_awaited_once()

    async def test_poll_timeout_retains_pending_id(self) -> None:
        """Keep the accepted build discoverable after a polling timeout."""
        with TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            result = {"template_id": "tpl-test"}
            manager = AsyncMock()
            with self.assertRaisesRegex(RuntimeError, "still building"):
                await builder.wait_template(
                    manager, template("Pending"), 0.01, output, result
                )
            self.assertEqual(
                json.loads(output.read_text())["phase"], "Pending"
            )
            manager.delete_template.assert_not_awaited()

    async def test_success_uses_public_request_and_runs_restore_check(
        self,
    ) -> None:
        """Wait for publication and verify the restored immutable image."""
        with TemporaryDirectory() as directory:
            args = options(Path(directory) / "result.json")
            manager = AsyncMock()
            manager.create_template.return_value = template("Pending")
            manager.get_template.return_value = template("Succeeded")
            with (
                patch.object(SandboxManager, "create", return_value=manager),
                patch.object(builder.shutil, "which", return_value="tool"),
                patch.object(builder, "run_command"),
                patch.object(
                    builder, "build_image", return_value="image@" + DIGEST
                ),
                patch.object(builder.asyncio, "sleep", new_callable=AsyncMock),
                patch.object(
                    builder, "validate_template", new_callable=AsyncMock
                ) as validate,
                patch.dict(
                    "os.environ", {"OPENSANDBOX_API_KEY": "test-secret"}
                ),
            ):
                result = await builder.build_template(args)
            request = manager.create_template.call_args.args[0]
            self.assertIsInstance(request, CreateTemplateRequest)
            self.assertEqual(request.image, "image@" + DIGEST)
            self.assertEqual(request.format, "native")
            self.assertEqual(
                request.readiness.probe, "cmd://" + builder.CHECK_COMMAND
            )
            self.assertTrue(result["validated"])
            self.assertEqual(result["phase"], "Succeeded")
            validate.assert_awaited_once()
            self.assertNotIn("test-secret", args.output.read_text())
            manager.close.assert_awaited_once()

    async def test_bad_marker_fails_without_bootstrap_and_deletes_sandbox(
        self,
    ) -> None:
        """Reject an incompatible prepared marker instead of repairing it."""
        with TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            sandbox = AsyncMock()
            sandbox.id = "sandbox-validation"
            sandbox.files.read_bytes.return_value = b"{}"
            with patch.object(
                Sandbox, "create_from_template", return_value=sandbox
            ):
                with self.assertRaisesRegex(RuntimeError, "incompatible"):
                    await builder.validate_template(
                        "tpl-test", None, output, {}
                    )
            sandbox.commands.run.assert_not_awaited()
            sandbox.kill.assert_awaited_once()
            sandbox.close.assert_awaited_once()
            self.assertTrue(
                json.loads(output.read_text())["validation_sandbox_deleted"]
            )

    async def test_failed_gateway_command_deletes_sandbox(self) -> None:
        """A valid marker alone cannot establish live gateway readiness."""
        from agentscope.workspace._opensandbox._template import (
            prepared_template_state,
        )

        with TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            sandbox = AsyncMock()
            sandbox.id = "sandbox-validation"
            sandbox.files.read_bytes.return_value = json.dumps(
                prepared_template_state(5600)
            ).encode()
            sandbox.commands.run.return_value = SimpleNamespace(
                error=None, exit_code=1
            )
            with patch.object(
                Sandbox, "create_from_template", return_value=sandbox
            ):
                with self.assertRaisesRegex(RuntimeError, "unhealthy"):
                    await builder.validate_template(
                        "tpl-test", None, output, {}
                    )
            sandbox.kill.assert_awaited_once()
            sandbox.close.assert_awaited_once()

    async def test_failed_cleanup_never_records_deleted(self) -> None:
        """Surface failed deletion and retain the sandbox ID for cleanup."""
        from agentscope.workspace._opensandbox._template import (
            prepared_template_state,
        )

        with TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            sandbox = AsyncMock()
            sandbox.id = "sandbox-validation"
            sandbox.files.read_bytes.return_value = json.dumps(
                prepared_template_state(5600)
            ).encode()
            sandbox.commands.run.return_value = SimpleNamespace(
                error=None, exit_code=0
            )
            sandbox.kill.side_effect = RuntimeError("delete failed")
            with patch.object(
                Sandbox, "create_from_template", return_value=sandbox
            ):
                with self.assertRaisesRegex(RuntimeError, "delete failed"):
                    await builder.validate_template(
                        "tpl-test", None, output, {}
                    )
            saved = json.loads(output.read_text())
            self.assertEqual(saved["validation_sandbox_id"], sandbox.id)
            self.assertFalse(saved["validation_sandbox_deleted"])
            sandbox.close.assert_awaited_once()
