# -*- coding: utf-8 -*-
"""Test Kubernetes file reads against real tar without a cluster.

Only the exec transport is replaced with a local subprocess. Filename
parsing, archive generation, and K8sBackend extraction all run normally.
"""

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agentscope.tool import LocalBackend
from agentscope.workspace._k8s._k8s_backend import K8sBackend


@unittest.skipUnless(
    os.name == "posix" and shutil.which("tar") is not None,
    "requires POSIX paths and tar",
)
class TestK8sBackendReadFile(unittest.IsolatedAsyncioTestCase):
    """Exercise the backend's tar command and the returned file bytes."""

    async def asyncSetUp(self) -> None:
        """Use local files and exec while retaining the real read method."""
        # pylint: disable=consider-using-with
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name) / "parent with spaces"
        self.root.mkdir()
        self.backend = K8sBackend(
            api_client=None,
            namespace="test",
            pod_name="test",
            container_name="test",
            workdir=str(self.root),
        )
        transport = patch.object(
            self.backend,
            "exec_shell",
            side_effect=LocalBackend().exec_shell,
        )
        transport.start()
        self.addCleanup(transport.stop)
        # BSD tar on macOS otherwise adds AppleDouble metadata members,
        # which are not produced by tar in a Linux Pod.
        environment = patch.dict(os.environ, {"COPYFILE_DISABLE": "1"})
        environment.start()
        self.addCleanup(environment.stop)

    async def test_read_leading_hyphen_filenames(self) -> None:
        """Option-like names must be read as literal file operands."""
        for name in ("-notes.txt", "--version", "-C", "--", "-"):
            with self.subTest(name=name):
                path = self.root / name
                payload = name.encode() + b"\x00\xff\r\n"
                path.write_bytes(payload)
                self.assertEqual(
                    await self.backend.read_file(str(path)),
                    payload,
                )

    async def test_read_regular_binary_file(self) -> None:
        """Ordinary filenames and binary content keep their behavior."""
        path = self.root / "regular file.bin"
        payload = bytes(range(256))
        path.write_bytes(payload)
        self.assertEqual(await self.backend.read_file(str(path)), payload)

    async def test_read_missing_leading_hyphen_file(self) -> None:
        """A missing option-like filename still reports not found."""
        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file(str(self.root / "--missing"))
