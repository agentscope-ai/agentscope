# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Integration tests for :class:`K8sWorkspace`.

These tests exercise a real Kubernetes cluster (kind is used in CI).
The whole module is skipped when either:

* the current platform is not Linux, or
* ``K8S_TEST_IMAGE`` is not set (the pre-baked test image name), or
* ``kubectl`` is not on ``PATH`` (kind cluster not provisioned).

Each test uses a fresh ``workspace_id`` so PVCs/Pods never collide, and
``delete_pvc_on_close=True`` so no residue is left in the cluster.

The pre-baked image (see ``tests/docker/k8s_workspace_test.Dockerfile``)
ships the gateway script at ``GATEWAY_HOME``, so ``initialize()`` skips
bootstrap and each test finishes in ~15 s.
"""
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.workspace import K8sWorkspace
from agentscope.workspace._k8s._constants import (
    GATEWAY_HOME,
    POD_WORKDIR,
)

# ── cluster availability check ────────────────────────────────────

_TEST_IMAGE = os.getenv("K8S_TEST_IMAGE", "")
_TEST_NAMESPACE = os.getenv("K8S_TEST_NAMESPACE", "agentscope")
_KIND_AVAILABLE = (
    sys.platform.startswith("linux")
    and bool(_TEST_IMAGE)
    and shutil.which("kubectl") is not None
)
_SKIP_REASON = (
    "requires linux + K8S_TEST_IMAGE + kubectl (kind cluster). "
    "See tests/docker/k8s_workspace_test.Dockerfile and the "
    "``k8s-workspace-tests`` CI job."
)


def _new_workspace(**overrides: object) -> K8sWorkspace:
    """Construct a K8sWorkspace pinned to the pre-baked test image.

    Args:
        **overrides:
            Additional/replacement kwargs forwarded to
            :class:`K8sWorkspace`.

    Returns:
        `K8sWorkspace`:
            A workspace with a unique ``workspace_id``, tiny PVC, and
            ``delete_pvc_on_close=True`` so tests never leak state.
    """
    kwargs: dict[str, object] = {
        "workspace_id": f"test-{uuid.uuid4().hex[:8]}",
        "image": _TEST_IMAGE,
        "image_pull_policy": "Never",  # image is `kind load`-ed
        "namespace": _TEST_NAMESPACE,
        "storage_size": "100Mi",
        "delete_pvc_on_close": True,
    }
    kwargs.update(overrides)
    return K8sWorkspace(**kwargs)  # type: ignore[arg-type]


def _write_skill_dir(root: str, name: str, description: str) -> str:
    """Create a minimal, valid skill directory on the host.

    Args:
        root (`str`):
            Parent directory for the new skill.
        name (`str`):
            Skill name; also used as directory basename and written
            into the ``SKILL.md`` front matter.
        description (`str`):
            Skill description for the front matter.

    Returns:
        `str`:
            Absolute path to the created skill directory.
    """
    skill_dir = os.path.join(root, name)
    os.makedirs(skill_dir, exist_ok=True)
    with open(
        os.path.join(skill_dir, "SKILL.md"),
        "w",
        encoding="utf-8",
    ) as f:
        f.write(
            f"---\nname: {name}\ndescription: {description}\n---\n\n"
            f"# {name}\n\n{description}\n",
        )
    return skill_dir


# ── happy-path: bootstrap + skills + mcps + backend all share ────
# a single Pod to minimise total CI time. Each section below cleans
# up whatever it added so subsequent sections start from a known
# state. Destructive scenarios (reset, close) get their own class /
# workspace further down.


@unittest.skipUnless(_KIND_AVAILABLE, _SKIP_REASON)
class TestK8sWorkspaceHappyPath(IsolatedAsyncioTestCase):
    """Scenarios 1 + 2 + 3 + 7 exercised against a single Pod.

    Every section restores the workspace to a clean state before the
    next section runs, so ordering is not fragile — but ``unittest``
    runs test methods alphabetically anyway and there is only one
    test method here on purpose.
    """

    async def asyncSetUp(self) -> None:
        """Start a single workspace + host-side scratch dir."""
        # pylint: disable=consider-using-with
        self._skills_src = tempfile.TemporaryDirectory()
        self._ws = _new_workspace()
        await self._ws.initialize()
        self._backend = self._ws.get_backend()

    async def asyncTearDown(self) -> None:
        """Tear the workspace down and drop the scratch dir."""
        try:
            await self._ws.close()
        finally:
            self._skills_src.cleanup()

    async def test_workspace_end_to_end(self) -> None:
        """Cover bootstrap artefacts, skills CRUD, MCP CRUD + call, and
        every public backend API against one Pod.

        Sections:

        1. **Bootstrap** — verify every workdir / gateway artefact.
        2. **Skills**    — ``add_skill`` → ``list_skills`` → ``remove_skill``.
        3. **MCPs**      — register a pure-Python MCP (``uvx
           mcp-server-time``), list it, call ``list_raw_tools`` on
           it, deregister.
        4. **Backend**   — exec_shell (success / nonzero / cwd),
           write_file + read_file roundtrip (binary, nested path),
           file_exists, delete_path, join_path.
        """
        backend = self._backend
        ws = self._ws

        # ── 1. Bootstrap artefacts ───────────────────────────────
        artefact_paths = [
            f"{POD_WORKDIR}/data",
            f"{POD_WORKDIR}/skills",
            f"{POD_WORKDIR}/sessions",
            f"{POD_WORKDIR}/.mcp",
            f"{GATEWAY_HOME}/_mcp_gateway_app.py",
            f"{GATEWAY_HOME}/_glob_helper.py",
            f"{GATEWAY_HOME}/.venv/bin/python",
        ]
        artefact_state = {
            p: await backend.file_exists(p) for p in artefact_paths
        }
        self.assertDictEqual(
            artefact_state,
            {p: True for p in artefact_paths},
        )

        # ── 2. Skills CRUD ───────────────────────────────────────
        skill_path = _write_skill_dir(
            self._skills_src.name,
            "greeter",
            "Says hi.",
        )
        self.assertListEqual(await ws.list_skills(), [])

        await ws.add_skill(skill_path)
        skills = await ws.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(
            (skills[0].name, skills[0].description, skills[0].dir),
            ("greeter", "Says hi.", f"{POD_WORKDIR}/skills/greeter"),
        )

        await ws.remove_skill("greeter")
        self.assertListEqual(await ws.list_skills(), [])

        # ── 3. MCP CRUD + call (pure Python stdio MCP) ──────────
        mcp_client = MCPClient(
            name="time",
            is_stateful=True,
            mcp_config=StdioMCPConfig(
                command="uvx",
                args=["mcp-server-time"],
            ),
        )
        self.assertListEqual(await ws.list_mcps(), [])

        await ws.add_mcp(mcp_client)
        mcps = await ws.list_mcps()
        self.assertEqual(len(mcps), 1)
        self.assertEqual(mcps[0].name, "time")

        tools = await mcps[0].list_raw_tools()
        self.assertGreater(len(tools), 0)

        await ws.remove_mcp("time")
        self.assertListEqual(await ws.list_mcps(), [])

        # ── 4. Backend public API ───────────────────────────────
        # 4a. exec_shell success
        r_ok = await backend.exec_shell(["echo", "hello"])
        self.assertEqual(
            (r_ok.exit_code, r_ok.stdout.strip(), r_ok.ok()),
            (0, b"hello", True),
        )

        # 4b. exec_shell non-zero exit + stderr
        r_bad = await backend.exec_shell(
            ["sh", "-c", "echo boom >&2; exit 3"],
        )
        self.assertEqual(
            (r_bad.exit_code, r_bad.ok(), b"boom" in r_bad.stderr),
            (3, False, True),
        )

        # 4c. exec_shell honours cwd
        r_cwd = await backend.exec_shell(["pwd"], cwd=POD_WORKDIR)
        self.assertEqual(
            (r_cwd.exit_code, r_cwd.stdout.strip()),
            (0, POD_WORKDIR.encode()),
        )

        # 4d. write_file → read_file roundtrip, binary safe
        blob_path = f"{POD_WORKDIR}/data/blob.bin"
        blob = bytes(range(256)) * 16  # 4 KiB, all byte values
        await backend.write_file(blob_path, blob)
        self.assertEqual(await backend.read_file(blob_path), blob)

        # 4e. write_file mkdir -p
        nested = f"{POD_WORKDIR}/data/nested/deeper/note.txt"
        await backend.write_file(nested, b"nested")
        self.assertEqual(await backend.read_file(nested), b"nested")

        # 4f. read_file on missing path
        with self.assertRaises(FileNotFoundError):
            await backend.read_file(f"{POD_WORKDIR}/does_not_exist")

        # 4g. file_exists reflects delete_path (files + directories)
        tree_dir = f"{POD_WORKDIR}/data/tree"
        tree_child = f"{tree_dir}/child.txt"
        await backend.write_file(tree_child, b"child")
        await backend.delete_path(blob_path)
        await backend.delete_path(tree_dir)
        self.assertDictEqual(
            {
                blob_path: await backend.file_exists(blob_path),
                tree_dir: await backend.file_exists(tree_dir),
            },
            {blob_path: False, tree_dir: False},
        )

        # 4h. join_path is a pure string op
        self.assertEqual(
            backend.join_path("/workspace", "data", "x.txt"),
            "/workspace/data/x.txt",
        )


# ── destructive: reset ───────────────────────────────────────────


@unittest.skipUnless(_KIND_AVAILABLE, _SKIP_REASON)
class TestK8sWorkspaceReset(IsolatedAsyncioTestCase):
    """Scenario 5: ``reset`` wipes workspace state without killing the
    Pod or the gateway. Isolated because it is destructive."""

    async def asyncSetUp(self) -> None:
        """Host-side temp dir for a seed skill."""
        # pylint: disable=consider-using-with
        self._skills_src = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        """Drop the staging dir."""
        self._skills_src.cleanup()

    async def test_reset_clears_dirs_mcps_skills_but_keeps_gateway(
        self,
    ) -> None:
        """After ``reset``: dirs empty, MCPs gone, skills gone,
        gateway still alive.

        Verifies:

        1. Seed a skill file under ``skills/`` and a file under
           ``sessions/``.
        2. After ``reset``:
           - ``list_skills`` and ``list_mcps`` return empty lists,
           - ``sessions/`` and ``data/`` directories no longer exist,
           - the gateway script under ``GATEWAY_HOME`` is untouched
             (proves the Pod / gateway are still alive).
        """
        skill_path = _write_skill_dir(
            self._skills_src.name,
            "sample",
            "sample skill",
        )
        ws = _new_workspace()
        try:
            await ws.initialize()
            backend = ws.get_backend()

            # Seed state.
            await ws.add_skill(skill_path)
            await backend.write_file(
                f"{POD_WORKDIR}/sessions/s1/context.jsonl",
                b'{"msg": "hi"}\n',
            )
            self.assertEqual(len(await ws.list_skills()), 1)

            await ws.reset()

            state_after = {
                "skills": await ws.list_skills(),
                "mcps": await ws.list_mcps(),
                "sessions_exists": await backend.file_exists(
                    f"{POD_WORKDIR}/sessions",
                ),
                "data_exists": await backend.file_exists(
                    f"{POD_WORKDIR}/data",
                ),
                "gateway_alive": await backend.file_exists(
                    f"{GATEWAY_HOME}/_mcp_gateway_app.py",
                ),
            }
            self.assertDictEqual(
                state_after,
                {
                    "skills": [],
                    "mcps": [],
                    "sessions_exists": False,
                    "data_exists": False,
                    "gateway_alive": True,
                },
            )
        finally:
            await ws.close()


# ── destructive: close ───────────────────────────────────────────


@unittest.skipUnless(_KIND_AVAILABLE, _SKIP_REASON)
class TestK8sWorkspaceClose(IsolatedAsyncioTestCase):
    """Scenario 4: ``close`` tears down cleanly and is idempotent.
    Isolated because it terminates the workspace."""

    async def test_close_is_idempotent_and_releases_backend(self) -> None:
        """Two consecutive ``close`` calls succeed; backend is released.

        Verifies:

        1. After initialize, ``get_backend`` returns a backend.
        2. First ``close`` succeeds.
        3. Second ``close`` on the same workspace is a no-op (no
           exception raised) — matches the docstring contract
           "errors are swallowed so close is always safe to call".
        """
        ws = _new_workspace()
        await ws.initialize()
        self.assertIsNotNone(ws.get_backend())

        await ws.close()
        # Second close must not raise.
        await ws.close()


# ── local-only: get_instructions text ─────────────────────────────


class TestK8sWorkspaceInstructions(IsolatedAsyncioTestCase):
    """Scenario 6: ``get_instructions`` renders the workspace prompt.

    Pure local check — no cluster needed, so *not* skip-guarded.
    """

    async def test_get_instructions_substitutes_workdir_in_template(
        self,
    ) -> None:
        """Custom ``instructions`` template has ``{workdir}`` filled in."""
        ws = K8sWorkspace(
            workspace_id="prompt-only",
            instructions="Workdir is {workdir}; nothing else.",
        )
        self.assertEqual(
            await ws.get_instructions(),
            f"Workdir is {POD_WORKDIR}; nothing else.",
        )

    async def test_get_instructions_default_template_mentions_workdir(
        self,
    ) -> None:
        """Default template renders and mentions the Pod workdir."""
        ws = K8sWorkspace(workspace_id="prompt-default")
        text = await ws.get_instructions()
        # Assert on the full rendered string as a golden of the default
        # template — reviewers see intent at a glance.
        self.assertIn(POD_WORKDIR, text)
        self.assertIn("Kubernetes-based workspace", text)
        self.assertIn("data/", text)
        self.assertIn("skills/", text)
        self.assertIn("sessions/", text)


if __name__ == "__main__":
    unittest.main()
