# -*- coding: utf-8 -*-
"""上传文件格式转换（移植自 deer-flow utils/file_conversion.py）。

统一把可文本化的文件转换为 **Markdown (.md)**，与原始文件同目录共存。
其余格式标记为「不支持」，不落盘（调用方负责拒绝）。

支持：
- 文本/代码类（txt/md/csv/json/xml/log/各类源码） -> 复制为同名 .md
- PDF  -> .md (pdfplumber)
- Word (.docx/.doc) -> .md (python-docx)
- PPT  (.pptx/.ppt) -> .md (python-pptx)
- Excel (.xlsx/.xls) -> .md 表格 (openpyxl/pandas)
- HTML -> .md (html2text)
不支持：图片(已在接口层拒绝)、压缩包、二进制等 -> raise UnsupportedFileType
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .manager import UploadError

# 扩展名 -> (类别) 映射
_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".xml",
    ".log", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".sh", ".bash",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".c", ".cpp", ".h",
    ".hpp", ".cs", ".rb", ".php", ".rs", ".kt", ".swift", ".sql", ".r", ".scala",
    ".pl", ".lua", ".vim", ".dockerfile", ".gitignore", ".env",
}
_PDF_EXTS = {".pdf"}
_WORD_EXTS = {".docx", ".doc"}
_PPT_EXTS = {".pptx", ".ppt"}
_EXCEL_EXTS = {".xlsx", ".xls", ".xlsm"}
_HTML_EXTS = {".html", ".htm"}


class UnsupportedFileType(UploadError):
    """文件类型不在支持范围内。"""


def convert_file(real_path: Path) -> dict:
    """把上传文件转换为 .md。

    Args:
        real_path: 已落盘的源文件真实路径。

    Returns:
        dict: {
            "converted": bool,
            "target": str,            # .md 路径（converted=False 时为 ""）
            "format": str,            # 源格式类别
            "error": str | None,
        }
    """
    real_path = Path(real_path)
    ext = real_path.suffix.lower()
    md_path = real_path.with_suffix(".md")

    try:
        if ext in _TEXT_EXTS:
            # 文本类：原样复制为 .md（保持可读）
            shutil.copyfile(real_path, md_path)
            return {"converted": True, "target": str(md_path), "format": "text", "error": None}

        if ext in _PDF_EXTS:
            _convert_pdf(real_path, md_path)
            return {"converted": True, "target": str(md_path), "format": "pdf", "error": None}

        if ext in _WORD_EXTS:
            _convert_docx(real_path, md_path)
            return {"converted": True, "target": str(md_path), "format": "word", "error": None}

        if ext in _PPT_EXTS:
            _convert_pptx(real_path, md_path)
            return {"converted": True, "target": str(md_path), "format": "ppt", "error": None}

        if ext in _EXCEL_EXTS:
            _convert_excel(real_path, md_path)
            return {"converted": True, "target": str(md_path), "format": "excel", "error": None}

        if ext in _HTML_EXTS:
            _convert_html(real_path, md_path)
            return {"converted": True, "target": str(md_path), "format": "html", "error": None}

        raise UnsupportedFileType(f"unsupported file type: {ext}")

    except UnsupportedFileType:
        return {"converted": False, "target": "", "format": ext.lstrip("."), "error": "unsupported"}
    except Exception as exc:  # 转换失败不阻断上传
        return {"converted": False, "target": "", "format": ext.lstrip("."), "error": str(exc)}


# ---------------------------------------------------------------------------
# 具体格式转换实现（按需 import 第三方库，缺失则标记不支持）
# ---------------------------------------------------------------------------
def _convert_pdf(src: Path, dst: Path) -> None:
    try:
        import pdfplumber  # type: ignore
    except ImportError as e:
        raise UnsupportedFileType("pdfplumber not installed") from e
    parts: list[str] = []
    with pdfplumber.open(str(src)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            parts.append(f"## 第 {i} 页\n\n{text}")
    dst.write_text("\n\n".join(parts), encoding="utf-8")


def _convert_docx(src: Path, dst: Path) -> None:
    try:
        from docx import Document  # type: ignore
    except ImportError as e:
        raise UnsupportedFileType("python-docx not installed") from e
    doc = Document(str(src))
    lines: list[str] = []
    for para in doc.paragraphs:
        style = (para.style.name or "") if para.style else ""
        text = para.text.strip()
        if not text:
            continue
        if style.startswith("Heading"):
            level = "".join(filter(str.isdigit, style)) or "1"
            lines.append(f"{'#' * min(int(level), 6)} {text}")
        else:
            lines.append(text)
    dst.write_text("\n\n".join(lines), encoding="utf-8")


def _convert_pptx(src: Path, dst: Path) -> None:
    try:
        from pptx import Presentation  # type: ignore
    except ImportError as e:
        raise UnsupportedFileType("python-pptx not installed") from e
    prs = Presentation(str(src))
    lines: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        lines.append(f"## Slide {i}")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    t = "".join(r.text for r in p.runs).strip()
                    if t:
                        lines.append(t)
    dst.write_text("\n\n".join(lines), encoding="utf-8")


def _convert_excel(src: Path, dst: Path) -> None:
    try:
        import openpyxl  # type: ignore
    except ImportError as e:
        raise UnsupportedFileType("openpyxl not installed") from e
    wb = openpyxl.load_workbook(str(src), read_only=True, data_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"## Sheet: {ws.title}")
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = [str(c) if c is not None else "" for c in rows[0]]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")
        for row in rows[1:]:
            cells = [str(c) if c is not None else "" for c in row]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    dst.write_text("\n".join(lines), encoding="utf-8")


def _convert_html(src: Path, dst: Path) -> None:
    try:
        import html2text  # type: ignore
    except ImportError as e:
        raise UnsupportedFileType("html2text not installed") from e
    h = html2text.HTML2Text()
    h.body_width = 0  # 不自动换行
    raw = src.read_text(encoding="utf-8", errors="ignore")
    dst.write_text(h.handle(raw), encoding="utf-8")
