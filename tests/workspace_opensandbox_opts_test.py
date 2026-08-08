# -*- coding: utf-8 -*-
# pylint: disable=protected-access, missing-function-docstring
# pylint: disable=too-few-public-methods
"""Test cases for :class:`OpenSandboxWorkspace` options and manager."""

import sys
import types
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch


class _FakeConnectionConfig:
    """Captured connection config kwargs for assertion."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _install_opensandbox_stubs() -> None:
    """Install stub ``opensandbox`` packages into sys.modules.

    The real SDK is an optional extra; for the unit tests we only need
    enough module structure that ``from opensandbox.X import Y``
    resolves without raising and that our ``patch("opensandbox.X.Y")``
    targets can be looked up.
    """
    top = sys.modules.setdefault(
        "opensandbox",
        types.ModuleType("opensandbox"),
    )

    class _SandboxCls:
        """Sandbox stub with ``create`` so ``patch`` can replace it."""

        @staticmethod
        async def create(**_kwargs: object) -> object:  # pragma: no cover
            return MagicMock()

        @staticmethod
        async def resume(**_kwargs: object) -> object:  # pragma: no cover
            return MagicMock()

        @staticmethod
        async def connect(**_kwargs: object) -> object:  # pragma: no cover
            return MagicMock()

        async def pause(self) -> None:  # pragma: no cover
            return None

        async def close(self) -> None:  # pragma: no cover
            return None

    class _SandboxManagerCls:
        """SandboxManager stub with ``create`` so patch can replace it."""

        @classmethod
        async def create(
            cls,
            **_kwargs: object,
        ) -> "_SandboxManagerCls":  # pragma: no cover
            return cls()

        async def list_sandbox_infos(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> object:  # pragma: no cover
            return MagicMock(sandbox_infos=[])

        async def close(self) -> None:  # pragma: no cover
            return None

    top.Sandbox = _SandboxCls  # type: ignore[attr-defined]
    top.SandboxManager = _SandboxManagerCls  # type: ignore[attr-defined]

    config = sys.modules.setdefault(
        "opensandbox.config",
        types.ModuleType("opensandbox.config"),
    )
    conn = sys.modules.setdefault(
        "opensandbox.config.connection",
        types.ModuleType("opensandbox.config.connection"),
    )
    setattr(config, "connection", conn)
    conn.ConnectionConfig = _FakeConnectionConfig  # type: ignore[attr-defined]

    models = sys.modules.setdefault(
        "opensandbox.models",
        types.ModuleType("opensandbox.models"),
    )
    sandboxes = sys.modules.setdefault(
        "opensandbox.models.sandboxes",
        types.ModuleType("opensandbox.models.sandboxes"),
    )
    setattr(models, "sandboxes", sandboxes)
    for name in (
        "NetworkPolicy",
        "SandboxInfo",
        "SandboxFilter",
        "SandboxState",
    ):
        setattr(sandboxes, name, MagicMock)


_install_opensandbox_stubs()


class TestOpenSandboxWorkspaceOpts(IsolatedAsyncioTestCase):
    """Verify ``use_server_proxy`` and ``volumes`` wiring."""

    async def test_connection_config_sets_use_server_proxy(self) -> None:
        """``use_server_proxy=True`` propagates to ``ConnectionConfig``."""
        from agentscope.workspace import OpenSandboxWorkspace

        ws_true = OpenSandboxWorkspace(use_server_proxy=True)
        ws_false = OpenSandboxWorkspace(use_server_proxy=False)
        ws_default = OpenSandboxWorkspace()

        cfg_true = ws_true._connection_config()
        cfg_false = ws_false._connection_config()
        cfg_default = ws_default._connection_config()

        self.assertIs(cfg_true.kwargs["use_server_proxy"], True)
        self.assertIs(cfg_false.kwargs["use_server_proxy"], False)
        self.assertIs(cfg_default.kwargs["use_server_proxy"], False)

    async def test_create_sandbox_forwards_volumes(self) -> None:
        """``volumes`` list is forwarded to ``Sandbox.create(volumes=...)``."""
        from agentscope.workspace import OpenSandboxWorkspace

        volumes = [
            {"type": "bind", "source": "/host", "target": "/sandbox"},
        ]
        ws = OpenSandboxWorkspace(
            workspace_id="ws-vol",
            volumes=volumes,
        )
        self.assertEqual(ws.volumes, volumes)

        captured = {}

        async def _fake_create(**kwargs: object) -> object:
            captured["kwargs"] = kwargs
            sb = MagicMock()
            sb.id = "sb-vol"
            return sb

        with patch("opensandbox.Sandbox.create", new=_fake_create):
            with patch.object(
                ws,
                "_wait_until_running",
                new_callable=AsyncMock,
            ):
                with patch.object(
                    ws,
                    "_find_existing_sandbox",
                    new_callable=AsyncMock,
                ) as find_m:
                    find_m.return_value = None
                    with patch(
                        "agentscope.workspace._opensandbox."
                        "_opensandbox_workspace.OpenSandboxBackend",
                    ) as be_cls_mock:
                        be_cls_mock.return_value = MagicMock()
                        await ws._provision_backend()

        self.assertIn("volumes", captured["kwargs"])
        self.assertEqual(captured["kwargs"]["volumes"], volumes)


if __name__ == "__main__":
    unittest.main()
