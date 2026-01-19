# -*- coding: utf-8 -*-
"""Web tools namespace (browser/gateway utilities).

Exports three independent tools with zero-deviation parameter schema:
- `web_fetch_markdown_via_gateway(url)`
- `web_pdf_ocr_markdown(url)`
- `web_html_render_markdown(url)`

Implementation details marked as TODO where applicable.
"""
from ._gateway import web_fetch_markdown_via_gateway
from ._pdf_ocr import web_pdf_ocr_markdown
from ._html_md import web_html_render_markdown

__all__ = [
    "web_fetch_markdown_via_gateway",
    "web_pdf_ocr_markdown",
    "web_html_render_markdown",
]
