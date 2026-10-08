# -*- coding: utf-8 -*-
"""FastSandbox workspace configuration and checkpoint lifecycle tests."""

import os
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
        with patch.dict(os.environ, {"OPENSANDBOX_TEMPLATE_ID": "tpl-test"}):
            workspace = OpenSandboxWorkspace()
        self.assertEqual(workspace.template_id, "tpl-test")
        self.assertIsNone(workspace.image)

    def test_missing_template_fails_early(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "FastSandbox requires"):
                OpenSandboxWorkspace()

    def test_explicit_image_ignores_environment_template(self) -> None:
        with patch.dict(os.environ, {"OPENSANDBOX_TEMPLATE_ID": "tpl-test"}):
            workspace = OpenSandboxWorkspace(image="python:3.11-slim")
        self.assertIsNone(workspace.template_id)
        self.assertEqual(workspace.image, "python:3.11-slim")

    def test_conflicting_sources_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            OpenSandboxWorkspace(
                template_id="tpl-test", image="python:3.11-slim"
            )

    def test_template_workload_overrides_rejected(self) -> None:
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
