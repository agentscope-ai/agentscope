## Description

Fix `_to_openai_image_url` to handle local image files without file extensions.

## Problem

When a local image file has no extension (e.g., `/app/downloads/download`), `_to_openai_image_url` raises:

```
TypeError: "/app/downloads/download" should end with (.png, .jpg, .jpeg, .gif, .webp).
```

This happens when:
- Downloading images from URLs without file extension in the path (e.g., QQ multimedia URLs like `https://multimedia.nt.qq.com.cn/download?appid=...&rkey=...`)
- The downloaded file has no extension

## Solution

Use `imghdr` to detect file type when extension is missing:

```python
# No extension - detect file type using imghdr
detected_type = imghdr.what(raw_url)
if detected_type and detected_type in imghdr_to_mime:
    with open(raw_url, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode("utf-8")
    mime_type = imghdr_to_mime[detected_type]
    return f"data:{mime_type};base64,{base64_image}"
```

## Changes

- Added `imghdr` import and MIME type mapping
- Added fallback logic to detect image type by content when file has no extension
- Supported formats: png, jpeg, gif, webp, bmp

## Related Issues

Fixes #1336

## Checklist

- [x] Code follows the project style guidelines
- [x] Changes are minimal and focused on the fix
- [x] The fix handles the edge case described in the issue
