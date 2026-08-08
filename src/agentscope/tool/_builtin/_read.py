# -*- coding: utf-8 -*-
"""The read tool in agentscope."""
import base64
import fnmatch
import io
from typing import Any, List, Literal

from .._base import ToolBase, ToolMiddlewareBase
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionRule,
)
from .._response import ToolChunk
from ...message import (
    TextBlock,
    DataBlock,
    Base64Source,
    ToolResultState,
)
from ...state import AgentState
from ._backend import BackendBase, _normalize_newlines

_DATA_MEDIA_EXTENSIONS: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".ico": "image/x-icon",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
}


class Read(ToolBase):
    """The read tool."""

    name: str = "Read"
    """The tool name presented to the agent."""

    # pylint: disable=line-too-long
    description: str = """Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters
- Results are returned using cat -n format, with line numbers starting at 1
- This tool allows you to read images (eg PNG, JPG, GIF, WebP), audio (MP3, WAV, OGG), and video (MP4, WebM) files. Binary data files are returned as base64-encoded DataBlocks.
- This tool can read PDF files (.pdf). Text is extracted per page. You can optionally provide the pages parameter to read specific pages.
- For other text-based files, content is returned with line numbers."""  # noqa: E501
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "Optional 1-based line number to start reading "
                "from. Only applies to plain text files (default: 1)",
                "default": 1,
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": "Optional maximum number of lines to read. "
                "Only applies to plain text files (default: 2000, max: 2000)",
                "default": 2000,
                "maximum": 2000,
                "minimum": 1,
            },
            "pages": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "description": "Optional list of 1-based page numbers to "
                "read from a PDF file. If not provided, all pages are read.",
            },
        },
        "required": ["file_path"],
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = True

    _IMAGE_FORMAT_MAP: dict[str, tuple[str, str]] = {
        "png": ("PNG", "image/png"),
        "jpeg": ("JPEG", "image/jpeg"),
    }

    def __init__(
        self,
        max_line_characters: int = 2000,
        image_format: Literal["png", "jpeg"] | None = None,
        middlewares: List[ToolMiddlewareBase] | None = None,
        backend: BackendBase | None = None,
    ) -> None:
        """Initialize the read tool.

        Args:
            max_line_characters (`int`, defaults to 2000):
                The maximum number of characters to include
                for each line when reading files. Lines longer
                than this will be truncated with a "[truncated]"
                suffix.
            image_format (`Literal["png","jpeg"] | None`,
                optional):
                Target format for image conversion. Accepts
                ``"png"`` or ``"jpeg"``. When ``None`` (default),
                images are returned in their original format.
                Requires Pillow when set.
            middlewares (`List[ToolMiddlewareBase] | None`,
                optional):
                Tool middlewares wrapping the tool execution.
            backend (`BackendBase | None`, optional):
                The sandbox backend to use for file I/O. When
                ``None``, a :class:`LocalBackend` is created.
        """
        from ._backend import LocalBackend

        if (
            image_format is not None
            and image_format not in self._IMAGE_FORMAT_MAP
        ):
            raise ValueError(
                f"image_format must be 'png', 'jpeg', or "
                f"None, got '{image_format}'",
            )

        super().__init__(middlewares=middlewares)
        self._max_line_characters = max_line_characters
        self._image_format = image_format
        self._backend = backend or LocalBackend()

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for file reading.

        Read is a read-only tool. In EXPLORE mode the engine already handles
        the ALLOW via _check_explore_mode, so here we just return PASSTHROUGH
        to let the engine continue with rule matching.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="File reading is read-only.",
        )

    async def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        """Check if a permission rule matches the file path.

        Matches rule_content as a glob pattern against the "file_path"
        parameter using fnmatch. If rule_content is None, matches all
        invocations (tool-name-level rule).

        Args:
            rule_content (`str | None`):
                Glob pattern to match against the file path (e.g., "src/**"),
                or None to match all invocations
            tool_input (`dict[str, Any]`):
                The tool input data containing "file_path" key

        Returns:
            `bool`:
                True if the glob pattern matches the file path, False otherwise
        """
        # None = tool-name-level rule, matches everything
        if rule_content is None:
            return True

        file_path = tool_input.get("file_path", "")
        if not file_path:
            return False
        return fnmatch.fnmatch(file_path, rule_content)

    async def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        """Generate suggested permission rules for the file path.

        Suggests a glob pattern covering the parent directory of the file,
        allowing the user to grant permission for the entire directory at once.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data containing "file_path" key

        Returns:
            `List[PermissionRule]`:
                A single suggested rule covering the parent directory
                (e.g., file "/src/main.py" -> rule "src/**")
        """
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return []

        parent = self._backend.dirname(file_path)
        # Glob patterns are POSIX-style strings (matched by fnmatch),
        # not real filesystem paths — do NOT use backend.join_path here.
        pattern = (parent.rstrip("/\\") + "/**") if parent else "**"

        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=pattern,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    async def call(  # type: ignore[override]
        self,
        file_path: str,
        offset: int = 1,
        limit: int = 2000,
        pages: List[int] | None = None,
        _agent_state: AgentState | None = None,
    ) -> ToolChunk:
        """Read a file and return content as appropriate block types.

        Dispatches to format-specific readers based on file extension:
        - PDF: text extraction via RAG parser
        - Image/audio/video: base64-encoded DataBlock
        - Other: line-numbered text (TextBlock)
        """

        # Validate file_path is absolute
        if not self._backend.isabs(file_path):
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: file_path must be an absolute path, "
                        f"got: {file_path}",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        # Check file exists
        if not await self._backend.file_exists(file_path):
            return ToolChunk(
                content=[
                    TextBlock(text=f"Error: File does not exist: {file_path}"),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        # Check it's not a directory
        if await self._backend.is_dir(file_path):
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: Path is a directory, not a file: "
                        f"{file_path}",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        # Determine file type by extension
        ext = self._get_extension(file_path).lower()

        if ext == ".pdf":
            return await self._read_pdf(file_path, pages)
        if ext in _DATA_MEDIA_EXTENSIONS:
            return await self._read_media_file(file_path, ext)

        return await self._read_text_file(
            file_path,
            offset,
            limit,
            _agent_state,
        )

    def _get_extension(self, file_path: str) -> str:
        """Extract file extension from path."""
        # pylint: disable=protected-access
        return self._backend._path_module.splitext(file_path)[1]

    async def _read_media_file(
        self,
        file_path: str,
        ext: str,
    ) -> ToolChunk:
        """Read a binary media file and return as DataBlock."""
        media_type = _DATA_MEDIA_EXTENSIONS[ext]

        try:
            raw = await self._backend.read_file(file_path)

            if (
                media_type.startswith("image/")
                and self._image_format is not None
            ):
                from PIL import Image

                pil_fmt, media_type = self._IMAGE_FORMAT_MAP[
                    self._image_format
                ]
                img = Image.open(io.BytesIO(raw))
                if pil_fmt == "JPEG" and img.mode in (
                    "RGBA",
                    "P",
                ):
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format=pil_fmt)
                raw = buf.getvalue()

            encoded = base64.b64encode(raw).decode(
                "ascii",
            )

            return ToolChunk(
                content=[
                    DataBlock(
                        source=Base64Source(
                            data=encoded,
                            media_type=media_type,
                        ),
                        name=self._backend.basename(file_path),
                    ),
                ],
                state=ToolResultState.RUNNING,
                is_last=True,
            )
        except Exception as e:
            return ToolChunk(
                content=[
                    TextBlock(text=f"Error reading file: {str(e)}"),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

    async def _read_pdf(
        self,
        file_path: str,
        pages: List[int] | None = None,
    ) -> ToolChunk:
        """Read a PDF file, extract text, and return as TextBlock."""
        try:
            from ...rag import PDFParser
        except ImportError:
            return ToolChunk(
                content=[
                    TextBlock(
                        text="Error: PDF reading requires the "
                        "'rag' extra. Install with: "
                        'pip install "agentscope[rag]"',
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        try:
            raw = await self._backend.read_file(file_path)

            parser = PDFParser()
            sections = await parser.parse(
                raw,
                self._backend.basename(file_path),
            )

            total_pages = len(sections)
            if pages is not None:
                target_pages = [p for p in pages if 1 <= p <= total_pages]
                if not target_pages:
                    return ToolChunk(
                        content=[
                            TextBlock(
                                text=f"Error: No valid pages in {pages}. "
                                f"PDF has {total_pages} page(s).",
                            ),
                        ],
                        state=ToolResultState.ERROR,
                        is_last=True,
                    )
                sections = [
                    s
                    for s in sections
                    if s.metadata.get("page") in target_pages
                ]

            text_parts: list[str] = []
            for s in sections:
                page_num = s.metadata.get("page", "?")
                text_parts.append(
                    f"--- Page {page_num}/{total_pages} ---\n"
                    f"{s.content.text}",
                )

            result = "\n\n".join(text_parts)
            return ToolChunk(
                content=[TextBlock(text=result)],
                state=ToolResultState.RUNNING,
                is_last=True,
            )

        except Exception as e:
            return ToolChunk(
                content=[
                    TextBlock(text=f"Error reading PDF: {str(e)}"),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

    async def _read_text_file(
        self,
        file_path: str,
        offset: int,
        limit: int,
        _agent_state: AgentState | None,
    ) -> ToolChunk:
        """Read a text file and return with line numbers."""
        try:
            # Read file content via backend
            lines = None
            if _agent_state is not None:
                cache = await _agent_state.tool_context.get_cache(file_path)
                if cache is not None:
                    lines = cache.lines

            if lines is None:
                raw = await self._backend.read_file(file_path)
                content_str = raw.decode("utf-8", errors="replace")
                # Normalize CRLF/CR so cached lines end in "\n" regardless
                # of the platform the file was written on (Windows text
                # files use "\r\n").
                content_str = _normalize_newlines(content_str)
                lines = content_str.splitlines(keepends=True)

                # Cache file if state is provided
                if _agent_state is not None:
                    await _agent_state.tool_context.cache_file(
                        file_path=file_path,
                        lines=lines,
                    )

            # Apply offset and limit (offset is 1-based)
            start_idx = offset - 1
            end_idx = start_idx + limit
            selected_lines = lines[start_idx:end_idx]

            # Format with line numbers (6-char padded + tab + content)
            formatted_lines = []
            for i, line in enumerate(selected_lines, start=offset):
                # Remove trailing newline if present
                line_content = line.rstrip("\n\r")

                # Truncate lines longer than max_line_characters
                if len(line_content) > self._max_line_characters:
                    line_content = (
                        line_content[: self._max_line_characters]
                        + "[truncated]"
                    )

                # Format: 6-char padded line number + tab + content
                formatted_line = f"{i:6d}\t{line_content}"
                formatted_lines.append(formatted_line)

            # Join all lines
            result = "\n".join(formatted_lines)

            return ToolChunk(
                content=[TextBlock(text=result)],
                state=ToolResultState.RUNNING,
                is_last=True,
            )

        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Error reading file: {str(e)}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
