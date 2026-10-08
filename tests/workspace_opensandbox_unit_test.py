# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""FastSandbox workspace configuration and checkpoint lifecycle tests."""

import os
import posixpath
from importlib.util import find_spec
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless
from unittest.mock import AsyncMock, patch

from agentscope.workspace import OpenSandboxWorkspace
from agentscope.app.workspace_manager import OpenSandboxWorkspaceManager


def info(state: str) -> SimpleNamespace:
    """Return the sandbox fields needed by lifecycle operations."""
    return SimpleNamespace(
        id="sandbox-test", status=SimpleNamespace(state=state)
    )


class TestFastSandboxConfiguration(TestCase):
    """Template mode is the default; image mode must be explicit."""

    def test_environment_template_is_default(self) -> None:
        """Use the configured FastSandbox template by default."""
        with patch.dict(os.environ, {"OPENSANDBOX_TEMPLATE_ID": "tpl-test"}):
            workspace = OpenSandboxWorkspace()
        self.assertEqual(workspace.template_id, "tpl-test")
        self.assertIsNone(workspace.image)

    def test_missing_template_fails_early(self) -> None:
        """Reject implicit image creation when no template is configured."""
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "FastSandbox requires"):
                OpenSandboxWorkspace()

    def test_explicit_image_ignores_environment_template(self) -> None:
        """Keep explicit legacy image mode available."""
        with patch.dict(os.environ, {"OPENSANDBOX_TEMPLATE_ID": "tpl-test"}):
            workspace = OpenSandboxWorkspace(image="python:3.11-slim")
        self.assertIsNone(workspace.template_id)
        self.assertEqual(workspace.image, "python:3.11-slim")

    def test_conflicting_sources_rejected(self) -> None:
        """Reject conflicting template and image sources."""
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            OpenSandboxWorkspace(
                template_id="tpl-test", image="python:3.11-slim"
            )

    def test_template_workload_overrides_rejected(self) -> None:
        """Require workload changes to be baked into a template."""
        for override in (
            {"env": {"A": "B"}},
            {"resource": {"cpu": "2"}},
            {"entrypoint": ["sh"]},
        ):
            with self.subTest(override=override):
                with self.assertRaisesRegex(ValueError, "templates fix"):
                    OpenSandboxWorkspace(template_id="tpl-test", **override)


@skipUnless(
    find_spec("opensandbox"), "Install the OpenSandbox workspace extra"
)
class TestFastSandboxLifecycle(IsolatedAsyncioTestCase):
    """Readiness acceptance must not be mistaken for durable pause."""

    async def test_create_uses_template_and_workspace_metadata(self) -> None:
        """Create uses template and workspace metadata."""
        workspace = OpenSandboxWorkspace(
            template_id="tpl-test",
            workspace_id="workspace-test",
            sandbox_metadata={"origin": "unit-test"},
        )
        with patch(
            "opensandbox.Sandbox.create_from_template",
            new_callable=AsyncMock,
            create=True,
        ) as create:
            await workspace._create_sandbox()
        kwargs = create.await_args.kwargs
        self.assertEqual(kwargs["template_id"], "tpl-test")
        self.assertEqual(
            kwargs["metadata"]["agentscope-workspace-id"], "workspace-test"
        )
        self.assertEqual(kwargs["metadata"]["origin"], "unit-test")
        self.assertNotIn("image", kwargs)
        self.assertNotIn("resource", kwargs)

    async def test_pause_waits_for_durable_checkpoint_before_close(
        self,
    ) -> None:
        """Pause waits for durable checkpoint before close."""
        workspace = OpenSandboxWorkspace(template_id="tpl-test")
        sandbox = SimpleNamespace(
            id="sandbox-test", pause=AsyncMock(), close=AsyncMock()
        )
        workspace._sandbox = sandbox
        manager = SimpleNamespace(
            get_sandbox_info=AsyncMock(
                side_effect=[info("Running"), info("Pausing"), info("Paused")]
            ),
            close=AsyncMock(),
        )

        async def poll_delay(_seconds: float) -> None:
            sandbox.close.assert_not_awaited()

        with (
            patch(
                "opensandbox.SandboxManager.create",
                new_callable=AsyncMock,
                return_value=manager,
            ),
            patch("asyncio.sleep", side_effect=poll_delay),
        ):
            await workspace._teardown_backend()
        sandbox.pause.assert_awaited_once()
        self.assertEqual(manager.get_sandbox_info.await_count, 3)
        manager.close.assert_awaited_once()
        sandbox.close.assert_awaited_once()
        self.assertIsNone(workspace._sandbox)

    async def test_pausing_workspace_is_waited_then_resumed(self) -> None:
        """Pausing workspace is waited then resumed."""
        workspace = OpenSandboxWorkspace(template_id="tpl-test")
        with (
            patch.object(
                workspace, "_wait_until_paused", new_callable=AsyncMock
            ) as wait,
            patch(
                "opensandbox.Sandbox.resume", new_callable=AsyncMock
            ) as resume,
        ):
            await workspace._attach_existing_sandbox(info("Pausing"))
        wait.assert_awaited_once_with("sandbox-test")
        self.assertEqual(
            resume.await_args.kwargs["sandbox_id"], "sandbox-test"
        )

    async def test_checkpoint_timeout_closes_manager(self) -> None:
        """Checkpoint timeout closes manager."""
        workspace = OpenSandboxWorkspace(
            template_id="tpl-test", timeout_seconds=0
        )
        manager = SimpleNamespace(
            get_sandbox_info=AsyncMock(), close=AsyncMock()
        )
        with patch(
            "opensandbox.SandboxManager.create",
            new_callable=AsyncMock,
            return_value=manager,
        ):
            with self.assertRaises(TimeoutError):
                await workspace._wait_until_paused("sandbox-test")
        manager.close.assert_awaited_once()

    async def test_failed_checkpoint_is_not_resumed(self) -> None:
        """Failed checkpoint is not resumed."""
        workspace = OpenSandboxWorkspace(template_id="tpl-test")
        manager = SimpleNamespace(
            get_sandbox_info=AsyncMock(return_value=info("Failed")),
            close=AsyncMock(),
        )
        with (
            patch(
                "opensandbox.SandboxManager.create",
                new_callable=AsyncMock,
                return_value=manager,
            ),
            patch(
                "opensandbox.Sandbox.resume", new_callable=AsyncMock
            ) as resume,
        ):
            with self.assertRaisesRegex(RuntimeError, "cannot pause"):
                await workspace._attach_existing_sandbox(info("Pausing"))
        resume.assert_not_awaited()
        manager.close.assert_awaited_once()

    async def test_manager_forwards_template(self) -> None:
        """Manager forwards template."""
        manager = OpenSandboxWorkspaceManager(template_id="tpl-test")
        target = (
            "agentscope.app.workspace_manager."
            "_opensandbox_workspace_manager.OpenSandboxWorkspace"
        )
        with patch(target) as workspace:
            workspace.return_value.initialize = AsyncMock()
            await manager._build_and_start(
                workspace_id="ws", user_id="user", agent_id="agent"
            )
        self.assertEqual(workspace.call_args.kwargs["template_id"], "tpl-test")
        self.assertEqual(
            workspace.call_args.kwargs["sandbox_metadata"],
            {"agentscope-user-id": "user", "agentscope-agent-id": "agent"},
        )

    async def test_lookup_includes_inflight_pause_and_mode_metadata(
        self,
    ) -> None:
        """Lookup includes inflight pause and mode metadata."""
        for source, key in (
            ({"template_id": "tpl-test"}, "agentscope-workspace-id"),
            ({"image": "python:3.11-slim"}, "agentscope.workspace.id"),
        ):
            with self.subTest(source=source):
                workspace = OpenSandboxWorkspace(
                    workspace_id="workspace-test", **source
                )
                manager = SimpleNamespace(
                    list_sandbox_infos=AsyncMock(
                        return_value=SimpleNamespace(sandbox_infos=[])
                    ),
                    close=AsyncMock(),
                )
                with patch(
                    "opensandbox.SandboxManager.create",
                    new_callable=AsyncMock,
                    return_value=manager,
                ):
                    self.assertIsNone(await workspace._find_existing_sandbox())
                query = manager.list_sandbox_infos.await_args.args[0]
                self.assertEqual(query.metadata, {key: "workspace-test"})
                self.assertEqual(
                    {str(state).lower() for state in query.states},
                    {"running", "pausing", "paused"},
                )
                manager.close.assert_awaited_once()


class TestPreparedGateway(IsolatedAsyncioTestCase):
    """Only validated empty template workspaces may skip initialization."""

    def workspace(self, **options: object) -> OpenSandboxWorkspace:
        """Bind a fresh template workspace to a minimal fake backend."""
        workspace = OpenSandboxWorkspace(template_id="tpl-test", **options)
        workspace._fresh_template_sandbox = True
        workspace._backend = SimpleNamespace(
            read_file=AsyncMock(),
            join_path=posixpath.join,
        )
        workspace._setup_skills = AsyncMock()
        return workspace

    async def test_prepared_gateway_skips_normal_initialization(self) -> None:
        """Bind a validated gateway without repeating normal setup."""
        import json
        from agentscope.workspace._opensandbox._template import (
            prepared_template_state,
        )

        workspace = self.workspace()
        workspace._backend.read_file.return_value = json.dumps(
            prepared_template_state(workspace.gateway_port),
        ).encode()
        workspace._provision_backend = AsyncMock()
        workspace._restore_mcp_specs = AsyncMock()
        workspace._ensure_workspace_layout = AsyncMock()
        workspace._setup_mcp_gateway = AsyncMock()
        workspace._migrate_skill_layout = AsyncMock()
        gateway = SimpleNamespace(health=AsyncMock(return_value=True))
        with patch(
            "agentscope.workspace._opensandbox._opensandbox_workspace."
            "GatewayClient",
            return_value=gateway,
        ):
            await workspace.initialize()
        self.assertTrue(workspace.is_alive)
        self.assertIs(workspace._gateway, gateway)
        self.assertEqual(workspace._mcp_specs, {})
        gateway.health.assert_awaited_once()
        workspace._setup_skills.assert_awaited_once()
        for method in (
            workspace._restore_mcp_specs,
            workspace._ensure_workspace_layout,
            workspace._setup_mcp_gateway,
            workspace._migrate_skill_layout,
        ):
            method.assert_not_awaited()

    async def test_missing_or_invalid_marker_falls_back(self) -> None:
        """Keep bootstrap available for unprepared images."""
        for value in (FileNotFoundError(), b"broken", b"\xff", b"{}"):
            with self.subTest(value=value):
                workspace = self.workspace()
                if isinstance(value, Exception):
                    workspace._backend.read_file.side_effect = value
                else:
                    workspace._backend.read_file.return_value = value
                self.assertFalse(
                    await workspace._initialize_prepared_workspace(),
                )
                workspace._setup_skills.assert_not_awaited()

    async def test_incompatible_contract_falls_back(self) -> None:
        """Require matching layout, port and script versions."""
        import json
        from agentscope.workspace._opensandbox._template import (
            prepared_template_state,
        )

        changes = {
            "schema_version": 2,
            "gateway_port": 9999,
            "gateway_script_sha256": "old",
            "glob_helper_sha256": "old",
            "workdir": "/other",
        }
        for key, value in changes.items():
            with self.subTest(key=key):
                workspace = self.workspace()
                state = prepared_template_state(workspace.gateway_port)
                state[key] = value
                workspace._backend.read_file.return_value = json.dumps(state)
                self.assertFalse(
                    await workspace._initialize_prepared_workspace(),
                )

    async def test_unhealthy_gateway_is_closed_and_falls_back(self) -> None:
        """Fall back when the restored process is unhealthy."""
        import json
        from agentscope.workspace._opensandbox._template import (
            prepared_template_state,
        )

        workspace = self.workspace()
        workspace._backend.read_file.return_value = json.dumps(
            prepared_template_state(workspace.gateway_port),
        )
        gateway = SimpleNamespace(
            health=AsyncMock(return_value=False),
            aclose=AsyncMock(),
        )
        with patch(
            "agentscope.workspace._opensandbox._opensandbox_workspace."
            "GatewayClient",
            return_value=gateway,
        ):
            self.assertFalse(
                await workspace._initialize_prepared_workspace(),
            )
        gateway.aclose.assert_awaited_once()
        self.assertIsNone(workspace._gateway)

    async def test_existing_workspace_and_extra_pip_do_not_reuse(self) -> None:
        """Use normal setup when session state or extra packages matter."""
        for fresh, extras in ((False, []), (True, ["numpy"])):
            with self.subTest(fresh=fresh, extras=extras):
                workspace = self.workspace(extra_pip=extras)
                workspace._fresh_template_sandbox = fresh
                self.assertFalse(
                    await workspace._initialize_prepared_workspace(),
                )
                workspace._backend.read_file.assert_not_awaited()

    async def test_marker_transport_error_is_not_hidden(self) -> None:
        """Propagate transport failures when checking the marker."""
        workspace = self.workspace()
        workspace._backend.read_file.side_effect = RuntimeError("transport")
        with self.assertRaisesRegex(RuntimeError, "transport"):
            await workspace._initialize_prepared_workspace()

    async def test_restored_user_specs_use_normal_flow(self) -> None:
        """Restore user MCP declarations before resetting the gateway."""
        workspace = self.workspace()
        workspace._fresh_template_sandbox = False
        workspace._provision_backend = AsyncMock()
        persisted = {("agent", "session"): ["saved-mcp"]}
        workspace._restore_mcp_specs = AsyncMock(return_value=persisted)
        workspace._ensure_workspace_layout = AsyncMock()
        workspace._setup_mcp_gateway = AsyncMock()
        workspace._migrate_skill_layout = AsyncMock()
        await workspace.initialize()
        self.assertIs(workspace._mcp_specs, persisted)
        workspace._setup_mcp_gateway.assert_awaited_once()
        workspace._backend.read_file.assert_not_awaited()

    async def test_build_recipe_matches_runtime_contract(self) -> None:
        """Keep the standalone build helper aligned with runtime validation."""
        import importlib.util
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from agentscope.workspace._opensandbox._template import (
            prepared_template_state,
        )

        repo = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "prepare_gateway",
            repo / "examples/opensandbox_fastsandbox_template/"
            "prepare_gateway.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        package = SimpleNamespace(
            locate_file=lambda relative: repo / "src" / relative,
        )
        with (
            TemporaryDirectory() as root,
            patch.object(
                module,
                "distribution",
                return_value=package,
            ),
        ):
            state = module.prepare(Path(root))
            self.assertEqual(state, prepared_template_state(5600))
            self.assertTrue(
                (Path(root) / "workspace/skills/.seed").is_dir(),
            )
            self.assertFalse((Path(root) / "workspace/.mcp").exists())
