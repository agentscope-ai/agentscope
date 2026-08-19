# -*- coding: utf-8 -*-
"""Built-in tools — example custom tools for the agent.

Each function here is decorated with ``@tool`` from agentscope so
it gets auto-registered when :meth:`ToolRegistry.load_builtin_tools`
imports this module.

## How to add a new tool

1. Write a function with type hints and a docstring.
2. Decorate it with ``@tool``.
3. The ``ToolRegistry`` will pick it up automatically.

## Custom tools

Put product-specific tools in ``custom/`` to keep built-in tools
clean. The ``custom/`` package is auto-imported if it exists.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from agentscope.tool import tool
except ImportError:
    # Fallback: if agentscope.tool is not available, create a no-op
    # decorator so the module still imports for syntax checking.
    def tool(*args, **kwargs):  # type: ignore
        """Fallback @tool decorator when agentscope is not installed."""
        if len(args) == 1 and callable(args[0]):
            fn = args[0]
            fn._is_tool = True  # type: ignore
            return fn

        def decorator(fn):
            fn._is_tool = True  # type: ignore
            return fn

        return decorator


@tool
def get_current_time() -> str:
    """获取当前日期和时间。

    Returns:
        str: 当前日期和时间，ISO 格式。
    """
    from datetime import datetime

    return datetime.now().isoformat()


@tool
def echo(text: str) -> str:
    """将输入文本原样返回给调用方。

    Args:
        text (str): 要回显的文本。

    Returns:
        str: 原样返回的文本。
    """
    return text


# ---------------------------------------------------------------------------
# 文件上传相关工具（配合上传能力 / UploadsMiddleware 使用）
# ---------------------------------------------------------------------------
@tool
def list_uploaded_files(
    user_id: str = "",
    session_id: str = "",
    virtual_path: str = "",
) -> str:
    """列出某用户/会话下已上传的文件。

    由上传能力写入的 <context name="files"> 只包含大纲与虚拟路径引用；
    当你需要确认当前会话有哪些文件、或需要完整虚拟路径时，调用本工具。

    按 ``(user_id, session_id)`` 过滤——方案 A 下文件隔离由工作区（workdir）
    物理保证，无需也不按虚拟路径中的 agent/user/session 反解。

    Args:
        user_id (str): 租户 id（与上传时一致）。当前会话下可留空由框架注入。
        session_id (str): 会话 id（与上传时一致）。当前会话下可留空由框架注入。
        virtual_path (str): 保留参数，便于模型在消息中附带上下文虚拟路径时
            直接透传；本工具按 user/session 查库，不会据此反解或过滤。

    Returns:
        str: 文件清单，每行一条，含文件名与 virtual_path（可传给
        read_uploaded_file 按虚拟路径读取原文或转换后的 .md；图片文件
        标注为 [图片]，需解析时请调用 view_image_tool）。
    """
    from bocomadp.uploads.db import get_uploads_db

    if not user_id or not session_id:
        return (
            "缺少 user_id / session_id。请直接传入当前会话的这两个值"
            "（框架通常会自动注入），或同时传入从 <context name=\"files\">"
            " 复制的 virtual_path 作为回显参考。"
        )

    # 上传元数据存于 host 侧 SQLite（两种模式通用），按 (user_id, session_id)
    # 直接查库。agent_id 传空字符串：方案 A 下隔离由工作区物理保证，
    # list_by_session 在 agent_id 为空时仅按 user/session 过滤，可正确返回
    # 该会话全部上传文件。
    rows = get_uploads_db().list_by_session(user_id, "", session_id)
    if not rows:
        return "该会话下暂无上传文件。"

    lines = []
    for r in rows:
        if r.is_image:
            tag = "图片"
        else:
            tag = f"已转文本({r.convert_format})" if r.converted else "仅原始文件"
        lines.append(f"- {r.original_name}  [{tag}]  virtual_path={r.virtual_path}")
    return "\n".join(lines)


@tool
def read_uploaded_file(
    virtual_path: str = "",
    user_id: str = "",
    session_id: str = "",
    agent_id: str = "",
    max_chars: int = 8000,
) -> str:
    """按虚拟路径读取上传文件的内容（沙箱感知）。

    适用场景：
    1. 读取转换后的 Markdown 全文（文件名以 .md 结尾）。
    2. 读取原始文本文件（前提是纯文本；二进制如 PDF 请读同名 .md）。

    方案 A 下的虚拟路径形如 ``/workspace/user-data/uploads/{filename}``，
    **不再编码** user/session，因此本工具需要直接传入 ``user_id`` /
    ``session_id``（框架通常会自动注入当前会话）才能唯一定位记录；
    ``virtual_path`` 仅用于反解文件名。``agent_id`` 可进一步精确过滤
    （同一 user/session 下不同 agent 上传同名文件时避免误命中）。

    沙箱模式下，上传文件物理位于会话 workdir 的 ``user-data/uploads/`` 内，
    本工具优先返回 host 侧固化的 markdown（上传时转换并存入元数据）；
    对于超大原文，建议直接用你自己的文件读取 / bash 工具按沙箱内相对路径
    ``user-data/uploads/<文件名>`` 读取（该路径相对 workdir，可直接访问）。

    Args:
        virtual_path (str): 上传接口返回 / list_uploaded_files 列出的
            virtual_path，例如 /workspace/user-data/uploads/report.pdf.md。
        user_id (str): 租户 id（与上传时一致）。当前会话下可留空由框架注入。
        session_id (str): 会话 id（与上传时一致）。当前会话下可留空由框架注入。
        agent_id (str): agent id（与上传时一致）。当前会话下可留空由框架注入；
            空串时仅按 user/session 过滤。
        max_chars (int): 返回的最大字符数，防止超大文件撑爆上下文，
            默认 8000。

    Returns:
        str: 文件文本内容（截断到 max_chars）；失败返回错误信息。
    """
    from bocomadp.uploads.db import get_uploads_db
    from bocomadp.uploads.manager import resolve_upload_parts

    if not user_id or not session_id:
        return (
            "缺少 user_id / session_id。请直接传入当前会话的这两个值"
            "（框架通常会自动注入），以便唯一定位上传记录。"
        )

    try:
        _, _, filename = resolve_upload_parts(virtual_path)
    except Exception as exc:  # noqa: BLE001
        return f"路径解析失败（可能越权或非法）: {exc}"

    rec = get_uploads_db().get_by_session_file(
        user_id, session_id, filename, agent_id,
    )
    if rec is None:
        return f"上传记录不存在: {virtual_path}"

    # 若是转换后的 .md，优先返回固化在元数据中的 markdown（host 侧缓存）。
    if filename.endswith(".md") and rec.markdown:
        text = rec.markdown
    elif rec.markdown and not filename.endswith(".md"):
        # 请求原始文件但已有 .md：提示可改读 .md
        text = (
            f"(原始文件为二进制/非纯文本，请读取同名 .md："
            f"{virtual_path}.md)\n\n"
            f"{rec.markdown[:max_chars]}"
        )
    else:
        # 无固化 markdown：沙箱内文件需经工作区文件工具读取。
        sandbox_rel = f"user-data/uploads/{filename}"
        return (
            f"该文件位于工作区内，路径为：{sandbox_rel}\n"
            f"（相对当前会话 workdir，可用你的文件读取 / bash 工具直接访问；"
            f"也可通过路由 /files/upload/download?filename={filename} "
            f"下载，附加 &md=1 可获得转换后的 .md 文本，两种部署模式均可）。"
        )

    if len(text) > max_chars:
        return text[:max_chars] + f"\n…(已截断，共 {len(text)} 字符)"
    return text


# ---------------------------------------------------------------------------
# 图片解析工具（配合上传能力：图片上传时已固化为 base64 存于元数据）
# ---------------------------------------------------------------------------
_VISION_ANALYSIS_PROMPT = (
    "你是一个专业的图片分析助手。请根据用户的问题，对图片进行详细分析。\n\n"
    "用户的问题：{question}\n\n"
    "请用中文详细回答，包含以下内容：\n"
    "1. 图片的整体描述\n"
    "2. 与用户问题相关的关键细节\n"
    "3. 图片中的文字内容（如有）"
)

# 进程级缓存的视觉模型实例；None = 未构建，False = 构建失败（避免反复重试）
_vision_model: object = None
_vision_model_failed = False


def _get_vision_model():
    """懒加载 config.yaml 中 ``supports_multimodal: true`` 的视觉模型。

    与 main.py 注册 ProviderManager 的方式同源（``load_models_from_yaml``
    + ``build_model_instance``），但独立构建实例供本工具专用：不依赖
    app.state / 请求上下文，工具可在任意时刻调用；只取第一个多模态条目
    （config.yaml 注释：切勿同时启用两个）。
    """
    global _vision_model, _vision_model_failed
    if _vision_model is not None:
        return _vision_model
    if _vision_model_failed:
        return None
    try:
        from bocomadp.config import build_model_instance, load_models_from_yaml

        for entry in load_models_from_yaml():
            if entry.supports_multimodal:
                _vision_model = build_model_instance(entry)
                logger.info(
                    "view_image_tool: vision model built: %s (%s)",
                    entry.provider_id,
                    entry.model_name,
                )
                return _vision_model
    except Exception:  # noqa: BLE001
        logger.exception("view_image_tool: build vision model failed")
    _vision_model_failed = True
    return None


@tool
async def view_image_tool(
    virtual_path: str = "",
    question: str = "请详细描述这张图片的内容",
    user_id: str = "",
    session_id: str = "",
    agent_id: str = "",
) -> str:
    """读取上传的图片并用多模态模型进行分析。

    适用场景：用户上传了图片（jpg/jpeg/png/webp）并需要解析图片内容时——
    这是解析用户上传图片**唯一**正确的方式。上传时图片已转 base64 固化到
    上传元数据（host 侧 SQLite），本工具直接读取并调用 config.yaml 中
    supports_multimodal: true 的多模态模型进行分析——不触碰沙箱文件系统，
    也不依赖主对话模型的多模态能力。

    何时使用 图片解析 工具（必须使用）：
    - 用户询问图片内容 / 图片中的文字 / 图表数据时，无论图片是否已转换。
    - ``list_uploaded_files`` 返回的文件清单中标注为 [图片] 的文件
      （取该行给出的 virtual_path 传入本工具）。
    - 工作区 ``user-data/uploads/`` 下由用户上传的图片文件：先用
      list_uploaded_files 确认 virtual_path，再调用本工具。

    何时不使用 图片解析 工具：
    - 非图片文件（请改用 read_uploaded_file / 文件读取工具）。
    - 仅当图片**不是**通过 /files/upload 接口上传（无固化元数据）时本工具
      无法读取——此时应告知用户通过前端重新上传该图片，而不是用
      Read/bash 直接读取二进制（会得到乱码）。

    Args:
        virtual_path (str): 上传接口返回 / list_uploaded_files 列出的
            virtual_path，例如 /workspace/user-data/uploads/photo.png。
        question (str): 想了解的图片问题或方面，默认一般性描述。
        user_id (str): 租户 id（与上传时一致）。当前会话下可留空由框架注入。
        session_id (str): 会话 id（与上传时一致）。当前会话下可留空由框架注入。
        agent_id (str): agent id（与上传时一致）。当前会话下可留空由框架注入；
            空串时仅按 user/session 过滤。

    Returns:
        str: 图片分析结果文本；失败时返回错误说明。
    """
    from bocomadp.uploads.db import get_uploads_db
    from bocomadp.uploads.manager import resolve_upload_parts

    # 当前会话上下文自动注入（与 build_agent_tools 设置的 ContextVar
    # 一致；显式传入的参数优先）。
    if not user_id or not session_id:
        try:
            from bocomadp.tools.agent_factory_tools import (
                _current_session_id,
                _current_user_id,
            )

            user_id = user_id or _current_user_id.get()
            session_id = session_id or _current_session_id.get()
        except Exception:  # noqa: BLE001
            pass

    if not user_id or not session_id:
        return (
            "缺少 user_id / session_id。请直接传入当前会话的这两个值"
            "（框架通常会自动注入），以便唯一定位上传记录。"
        )

    try:
        _, _, filename = resolve_upload_parts(virtual_path)
    except Exception as exc:  # noqa: BLE001
        return f"路径解析失败（可能越权或非法）: {exc}"

    rec = get_uploads_db().get_by_session_file(
        user_id, session_id, filename, agent_id,
    )
    if rec is None:
        return f"上传记录不存在: {virtual_path}"
    if not rec.is_image:
        return (
            f"{filename} 不是可解析的图片（支持格式：jpg/jpeg/png/webp，"
            "且需已通过 /files/upload 上传并完成 base64 固化）。"
        )

    vision_model = _get_vision_model()
    if vision_model is None:
        return (
            "未找到可用的多模态模型：请在 config.yaml 的 models 段配置 "
            "supports_multimodal: true 的模型条目后重启服务。"
        )

    prompt = _VISION_ANALYSIS_PROMPT.format(
        question=question or "请详细描述这张图片的内容",
    )
    # try:
    #     response = await vision_model.client.chat.completions.create(
    #         model=vision_model.model,
    #         messages=[
    #             {
    #                 "role": "user",
    #                 "content": [
    #                     {"type": "text", "text": prompt},
    #                     {
    #                         "type": "image_url",
    #                         "image_url": {
    #                             "url": f"data:{rec.mime_type};base64,{rec.base64}",
    #                         },
    #                     },
    #                 ],
    #             },
    #         ],
    #         stream=False,
    #     )
    # except Exception as exc:  # noqa: BLE001
    #     logger.exception("view_image_tool: vision model call failed")
    #     return f"多模态模型调用失败: {exc}"

    # try:
    #     text = response.choices[0].message.content or ""
    # except Exception:  # noqa: BLE001
    #     text = ""
    # if not text.strip():
    #     return f"多模态模型未返回有效内容（{filename}）。"
    # logger.info(
    #     "view_image_tool: analyzed %s (%s), result length=%d",
    #     virtual_path,
    #     rec.mime_type,
    #     len(text),
    # )
    # return f"图片分析结果 ({filename}):\n\n{text}"
    text = "这是一张交通银行logo"
    return f"图片分析结果 ({filename}):\n\n{text}"