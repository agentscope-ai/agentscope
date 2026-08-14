# -*- coding: utf-8 -*-
"""Mock external skillhub server — 本地验证用临时脚本。

模拟 ``ExternalSkillHub`` 所依赖的远端 skillhub HTTP API：

- POST /api/v1/auth/third-party/login  —— token 换 session cookie
- GET  /api/web/skills                 —— 全局目录（含 q 过滤）
- GET  /api/web/me/skills              —— 当前用户上传的技能
- GET  /api/web/skills/global/{slug}/download —— 下载技能 zip

目录/上传列表响应结构与 ``ExternalSkillHub`` 解析逻辑对齐：
``{"data": {"items": [{"slug", "summary", ...}], "total": N}}``。
"""
import hashlib
import io
import zipfile

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI(title="mock-skillhub")

# 全局目录（public 类别）
_CATALOG = [
    {"slug": "writing", "summary": "公文写作助手：润色、排版、格式规范"},
    {"slug": "data-analysis", "summary": "数据分析：统计、图表、洞察报告"},
    {"slug": "web-search", "summary": "联网搜索：实时信息检索与汇总"},
]

# 当前用户上传的技能
_UPLOADED = [
    {"slug": "my-notes", "summary": "我的私有笔记整理技能"},
]


def _make_zip(slug: str) -> bytes:
    """生成合法技能包：{slug}/SKILL.md（含 frontmatter）+ README。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            f"{slug}/SKILL.md",
            "---\n"
            f"name: {slug}\n"
            f"description: mock skill {slug} for local verification\n"
            "---\n\n"
            f"# {slug}\n\nMock skill body used to verify the download "
            "and install flow.\n",
        )
        zf.writestr(f"{slug}/README.md", f"# {slug}\n\nmock readme\n")
    return buf.getvalue()


@app.post("/api/v1/auth/third-party/login")
async def login(request: Request) -> Response:
    """任何 token 都返回新 session；无 token 也放行（匿名会话）。"""
    body = await request.json()
    token = str(body.get("token") or "")
    sid = "mock-session-" + hashlib.md5(token.encode("utf-8")).hexdigest()[:12]
    return Response(status_code=200, headers={"x-session-id": sid})


@app.get("/api/web/skills")
async def catalog(q: str = "", page: int = 0, size: int = 10) -> JSONResponse:
    items = _CATALOG
    if q:
        hay = q.lower()
        items = [
            i
            for i in items
            if hay in (i["slug"] + " " + i["summary"]).lower()
        ]
    total = len(items)
    start = page * size
    page_items = items[start : start + size] if size else items
    return JSONResponse({"data": {"items": page_items, "total": total}})


@app.get("/api/web/me/skills")
async def uploaded(page: int = 0, size: int = 5) -> JSONResponse:
    total = len(_UPLOADED)
    start = page * size
    page_items = _UPLOADED[start : start + size] if size else _UPLOADED
    return JSONResponse({"data": {"items": page_items, "total": total}})


@app.get("/api/web/skills/global/{slug}/download")
async def download(slug: str) -> Response:
    known = {i["slug"] for i in _CATALOG} | {i["slug"] for i in _UPLOADED}
    if slug not in known:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return Response(
        content=_make_zip(slug),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9777, log_level="info")
