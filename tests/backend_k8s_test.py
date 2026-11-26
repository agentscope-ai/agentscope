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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from agentscope.tool import LocalBackend
from agentscope.workspace._k8s._k8s_backend import K8sBackend
from agentscope.workspace._overwrite import WriteUnconfirmedError


class TestK8sOverwrite(unittest.IsolatedAsyncioTestCase):
    """Deliver remote command status frames after sending a draft."""

    async def test_write_requires_terminal_status(self) -> None:
        """Only an explicit success frame acknowledges the write."""
        client = MagicMock()
        api = client.CoreV1Api.return_value
        socket_context = MagicMock()
        socket = socket_context.__aenter__.return_value
        socket.send_bytes = AsyncMock()
        api.connect_get_namespaced_pod_exec = AsyncMock(
            return_value=socket_context,
        )
        pod = K8sBackend(MagicMock(), "ns", "pod", "container", "/work")
        modules = {
            "kubernetes_asyncio": SimpleNamespace(client=client),
            "kubernetes_asyncio.stream": SimpleNamespace(
                WsApiClient=MagicMock(),
            ),
        }
        with patch.dict("sys.modules", modules):
            for frame, expected in (
                (b'\x03{"status":"Success"}', None),
                (b'\x03{"status":"Failure"}', RuntimeError),
                (b'\x03{"status":', WriteUnconfirmedError),
                (b"\x03{}", WriteUnconfirmedError),
                (b'\x01{"status":"Success"}', WriteUnconfirmedError),
                (None, WriteUnconfirmedError),
            ):
                with self.subTest(frame=frame):
                    socket.__aiter__.return_value = (
                        []
                        if frame is None
                        else [SimpleNamespace(type=2, data=frame)]
                    )
                    socket.send_bytes.reset_mock()
                    if expected is None:
                        await pod.overwrite_file("/work/doc", b"draft")
                    else:
                        with self.assertRaises(expected) as ctx:
                            await pod.overwrite_file("/work/doc", b"draft")
                        self.assertIs(type(ctx.exception), expected)
                    self.assertEqual(
                        socket.send_bytes.await_args_list,
                        [call(b"\0draft"), call(b"\0")],
                    )
            socket.send_bytes.side_effect = [None, ConnectionResetError()]
            with self.assertRaises(WriteUnconfirmedError):
                await pod.overwrite_file("/work/doc", b"draft")


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
