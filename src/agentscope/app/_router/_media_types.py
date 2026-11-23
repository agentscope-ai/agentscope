# -*- coding: utf-8 -*-
"""Media types the app serves inline, shared by the file routers."""

# The media types a browser may render inline. Anything else (notably
# text/html and image/svg+xml, which can run script) is forced to an
# attachment so a crafted upload cannot XSS the app origin. Raster
# image formats are enumerated rather than matched by ``image/``
# prefix precisely so that SVG never slips through.
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
