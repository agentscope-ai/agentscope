# -*- coding: utf-8 -*-
"""Media types the app serves inline, shared by the file routers."""

# Both routers preview these types. Enumerate raster image formats so
# that scriptable SVG documents are always served as attachments.
INLINE_MEDIA_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
    },
)

# Workspace file links also support native browser previews of JSON.
WORKSPACE_INLINE_MEDIA_TYPES = INLINE_MEDIA_TYPES | {"application/json"}
