# -*- coding: utf-8 -*-
"""OSS 打包下载接口测试矩阵（对齐 deer-flow test_downloads.py，全部 mock 不连 OSS）。

风格对齐 bocomadp 现有 tests/（pytest + unittest.mock，不经 TestClient）：
同步测试函数内用 asyncio.run() 调用 async 端点/函数（项目无 pytest-asyncio
依赖，pyproject 亦未声明）；storage/workspace_manager 用 AsyncMock。
"""
from __future__ import annotations

import asyncio
import io
import os
import zipfile
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException

from cryptography.fernet import Fernet

import bocomadp.routers.oss_download as mod

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeBackend:
    """最小 backend 桩：内存文件树，实现 oss_download 所需方法。"""

    def __init__(self, tree: dict) -> None:
        self._tree = tree

    def join_path(self, path: str, *paths: str) -> str:
        for p in paths:
            path = f"{path.rstrip('/')}/{p}"
        return path

    def _lookup(self, path: str):
        parts = [p for p in path.split("/") if p]
        node = self._tree
        for p in parts:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return None
        return node

    async def list_dir(self, path: str) -> list[str]:
        node = self._lookup(path)
        if node is None or isinstance(node, bytes):
            raise OSError("no such dir")
        return list(node.keys())

    async def is_dir(self, path: str) -> bool:
        return isinstance(self._lookup(path), dict)

    async def file_exists(self, path: str) -> bool:
        return isinstance(self._lookup(path), bytes)

    async def read_file(self, path: str) -> bytes:
        node = self._lookup(path)
        if isinstance(node, bytes):
            return node
        raise OSError("not a file")


def _tree_at(workdir: str, body: dict) -> dict:
    """把 body 嵌套到 workdir 路径下，模拟真实会话目录结构。"""
    root: dict = {}
    node = root
    for part in workdir.split("/"):
        if part:
            node = node.setdefault(part, {})
    node.update(body)
    return root


def _tree_with_report(layout: str, workdir: str = "/workspace/sessions/s1") -> dict:
    """按布局构造工作目录树；三种布局均含 uploads/*_intermediate 目录。

    - "uploads": 报告在 ① 上传中间目录
    - "outputs": 报告在 ② user-data/outputs
    - "deep":    报告在 workdir 深处（③ 递归兜底）
    """
    body = {
        "user-data": {
            "uploads": {"run_intermediate": {}},
        },
    }
    if layout == "uploads":
        body["user-data"]["uploads"]["run_intermediate"] = {
            "proofread_report.md": b"report-u",
        }
    elif layout == "outputs":
        body["user-data"]["outputs"] = {"proofread_report.md": b"report-o"}
    else:  # deep
        body["user-data"]["outputs"] = {}
        body["a"] = {"b": {"proofread_report.md": b"report-d"}}
    return _tree_at(workdir, body)


def _call_endpoint(
    tree: dict,
    *,
    x_user_id: str | None = "alice",
    session_id: str = "s1",
) -> mod.FileDownloadResponse:
    workspace = SimpleNamespace(
        workdir="/workspace/sessions/s1",
        get_backend=lambda: FakeBackend(tree),
    )
    storage = mock.AsyncMock()
    storage.get_session.return_value = SimpleNamespace(
        config=SimpleNamespace(workspace_id="ws1"),
    )
    wm = mock.AsyncMock()
    wm.get_workspace.return_value = workspace
    return asyncio.run(
        mod.file_download(
            agent_id="a1",
            session_id=session_id,
            x_user_id=x_user_id,
            storage=storage,
            workspace_manager=wm,
        ),
    )


def _fake_bucket() -> mock.MagicMock:
    bucket = mock.MagicMock()
    bucket.put_object.return_value = None
    bucket.sign_url.return_value = "http://oss.example/signed.zip"
    return bucket


@pytest.fixture(autouse=True)
def _patch_bucket():
    with mock.patch.object(mod, "_get_oss_bucket", return_value=_fake_bucket()):
        yield


# ---------------------------------------------------------------------------
# 端到端 / 错误路径
# ---------------------------------------------------------------------------


def test_happy_path_uploads_layout():
    resp = _call_endpoint(_tree_with_report("uploads"))
    assert resp.success == "true"
    assert resp.download_url == "http://oss.example/signed.zip"
    assert resp.error == ""


def test_happy_path_outputs_layout():
    resp = _call_endpoint(_tree_with_report("outputs"))
    assert resp.success == "true"
    assert resp.error == ""


def test_happy_path_deep_recursive_layout():
    resp = _call_endpoint(_tree_with_report("deep"))
    assert resp.success == "true"
    assert resp.error == ""


def test_missing_oss_key():
    with mock.patch.object(
        mod,
        "_get_oss_bucket",
        side_effect=HTTPException(500, "AGENTSCOPE_OSS_KEY is not configured"),
    ):
        resp = _call_endpoint(_tree_with_report("outputs"))
    assert resp.success == "false"
    assert "AGENTSCOPE_OSS_KEY" in resp.error


def test_no_intermediate_dir_outputs_fallback():
    # 无 uploads/*_intermediate 目录，但 outputs 有报告 → ② 级兜底应成功
    resp = _call_endpoint(
        _tree_at("/workspace/sessions/s1", {"user-data": {"outputs": {"proofread_report.md": b"x"}}}),
    )
    assert resp.success == "true"
    assert resp.error == ""


def test_no_intermediate_dir_no_file_anywhere():
    # 无 uploads/*_intermediate 且 outputs/workdir 均无报告 → 仍报找不到
    resp = _call_endpoint(
        _tree_at("/workspace/sessions/s1", {"user-data": {"outputs": {}}}),
    )
    assert resp.success == "false"
    assert "No downloadable files" in resp.error


def test_no_report_file():
    # uploads/*_intermediate 存在但无 report（outputs 也空）→ collect 404
    resp = _call_endpoint(
        _tree_at(
            "/workspace/sessions/s1",
            {"user-data": {"uploads": {"run_intermediate": {}}, "outputs": {}}},
        ),
    )
    assert resp.success == "false"
    assert "No downloadable files" in resp.error


def test_oss_upload_failure():
    bucket = _fake_bucket()
    bucket.put_object.side_effect = mod.oss2.exceptions.OssError(
        502, {}, b"", {"Code": "UploadFailed", "Message": "boom"},
    )
    with mock.patch.object(mod, "_get_oss_bucket", return_value=bucket):
        resp = _call_endpoint(_tree_with_report("outputs"))
    assert resp.success == "false"
    assert "OSS upload failed" in resp.error


def test_missing_x_user_id():
    resp = _call_endpoint(_tree_with_report("outputs"), x_user_id=None)
    assert resp.success == "false"
    assert "X-User-ID" in resp.error


def test_session_not_found():
    async def _missing(user_id, agent_id, session_id, storage, wm):
        raise HTTPException(404, f"Session {session_id!r} not found.")

    with mock.patch.object(mod, "_resolve_workspace", side_effect=_missing):
        resp = _call_endpoint(_tree_with_report("outputs"))
    assert resp.success == "false"
    assert "not found" in resp.error


def test_session_isolation():
    workspace_a = SimpleNamespace(
        workdir="/workspace/sessions/s1",
        get_backend=lambda: FakeBackend(_tree_with_report("uploads")),
    )
    workspace_b = SimpleNamespace(
        workdir="/workspace/sessions/s2",
        get_backend=lambda: FakeBackend(
            _tree_with_report("outputs", workdir="/workspace/sessions/s2"),
        ),
    )
    wm = mock.AsyncMock()
    wm.get_workspace.side_effect = [workspace_a, workspace_b]
    storage = mock.AsyncMock()
    storage.get_session.return_value = SimpleNamespace(
        config=SimpleNamespace(workspace_id="ws1"),
    )
    captured: list[bytes] = []

    def _fake_pkg(files, session_id):
        captured.append(files[0][1])
        return io.BytesIO(b"PK"), f"proofread_report_{session_id}.zip"

    with mock.patch.object(mod, "_package_zip", side_effect=_fake_pkg):
        asyncio.run(
            mod.file_download(
                agent_id="a1", session_id="s1", x_user_id="alice",
                storage=storage, workspace_manager=wm,
            ),
        )
        asyncio.run(
            mod.file_download(
                agent_id="a1", session_id="s2", x_user_id="alice",
                storage=storage, workspace_manager=wm,
            ),
        )
    assert captured == [b"report-u", b"report-o"]


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------


def test_find_report_dir_ok():
    tree = _tree_at(
        "/workspace/sessions/s1",
        {"user-data": {"uploads": {"run_intermediate": {}, "other": {}}}},
    )
    backend = FakeBackend(tree)
    got = asyncio.run(mod._find_report_dir("/workspace/sessions/s1", backend))
    assert got == "/workspace/sessions/s1/user-data/uploads/run_intermediate"


def test_find_report_dir_no_uploads():
    # uploads 目录不存在 → 返回 None，由 _collect_files 兜底
    backend = FakeBackend(_tree_at("/workspace/sessions/s1", {"user-data": {}}))
    got = asyncio.run(mod._find_report_dir("/workspace/sessions/s1", backend))
    assert got is None


def test_find_report_dir_no_match():
    # uploads 下无 *_intermediate → 返回 None，由 _collect_files 兜底
    backend = FakeBackend(
        _tree_at("/workspace/sessions/s1", {"user-data": {"uploads": {"docs": {}}}}),
    )
    got = asyncio.run(mod._find_report_dir("/workspace/sessions/s1", backend))
    assert got is None


def test_collect_uploads_first():
    backend = FakeBackend(_tree_with_report("uploads"))
    report_dir = "/workspace/sessions/s1/user-data/uploads/run_intermediate"
    files = asyncio.run(
        mod._collect_files("/workspace/sessions/s1", backend, report_dir),
    )
    assert files == [("proofread_report.md", b"report-u")]


def test_collect_outputs_fallback():
    backend = FakeBackend(_tree_with_report("outputs"))
    files = asyncio.run(
        mod._collect_files(
            "/workspace/sessions/s1",
            backend,
            "/workspace/sessions/s1/user-data/uploads/run_intermediate",
        ),
    )
    assert files == [("proofread_report.md", b"report-o")]


def test_collect_none_report_dir_outputs_fallback():
    # report_dir=None（无 *_intermediate）→ 跳过 ①，② outputs 兜底命中
    backend = FakeBackend(_tree_with_report("outputs"))
    files = asyncio.run(
        mod._collect_files("/workspace/sessions/s1", backend, None),
    )
    assert files == [("proofread_report.md", b"report-o")]


def test_collect_recursive_fallback():
    backend = FakeBackend(_tree_with_report("deep"))
    files = asyncio.run(
        mod._collect_files(
            "/workspace/sessions/s1",
            backend,
            "/workspace/sessions/s1/user-data/uploads/run_intermediate",
        ),
    )
    assert files == [("proofread_report.md", b"report-d")]


def test_collect_none_raises_404():
    backend = FakeBackend(_tree_at("/workspace/sessions/s1", {"user-data": {}}))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            mod._collect_files(
                "/workspace/sessions/s1",
                backend,
                "/workspace/sessions/s1/user-data/uploads/run_intermediate",
            ),
        )
    assert ei.value.status_code == 404


def test_package_zip_magic_and_name():
    buf, name = mod._package_zip([("proofread_report.md", b"hello")], "s1")
    assert buf.getvalue()[:2] == b"PK"
    assert name.startswith("proofread_report_")
    assert name.endswith("_s1.zip")
    with zipfile.ZipFile(buf) as zf:
        assert zf.read("proofread_report.md") == b"hello"


def test_upload_to_oss_returns_url():
    bucket = _fake_bucket()
    with mock.patch.object(mod, "_get_oss_bucket", return_value=bucket):
        url = mod._upload_to_oss(io.BytesIO(b"PK"), "proofread_report_x.zip")
    assert url == "http://oss.example/signed.zip"
    bucket.put_object.assert_called_once()
    key = bucket.put_object.call_args.args[0]
    assert key.startswith(mod._OSS_PATH)
    bucket.sign_url.assert_called_once()


def test_upload_to_oss_error_raises_502():
    bucket = _fake_bucket()
    bucket.put_object.side_effect = mod.oss2.exceptions.OssError(
        502, {}, b"", {"Code": "UploadFailed", "Message": "boom"},
    )
    with mock.patch.object(mod, "_get_oss_bucket", return_value=bucket):
        with pytest.raises(HTTPException) as ei:
            mod._upload_to_oss(io.BytesIO(b"PK"), "proofread_report_x.zip")
    assert ei.value.status_code == 502


def test_decrypt_credential_roundtrip():
    key = Fernet.generate_key().decode()
    enc = Fernet(key.encode()).encrypt(b"secret").decode()
    with mock.patch.dict(os.environ, {"AGENTSCOPE_OSS_KEY": key}):
        assert mod._decrypt_credential(enc) == "secret"


def test_decrypt_credential_missing_key():
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(HTTPException) as ei:
            mod._decrypt_credential("abc")
    assert ei.value.status_code == 500
    assert "AGENTSCOPE_OSS_KEY" in str(ei.value.detail)
