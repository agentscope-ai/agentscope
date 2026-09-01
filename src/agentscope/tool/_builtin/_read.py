# -*- coding: utf-8 -*-
"""The read tool in agentscope."""
import base64
import fnmatch
import os
import re
from typing import Any, List

from pydantic import Field

from .._base import ParamsBase, ToolBase, ToolMiddlewareBase
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

_IMAGE_EXTENSIONS: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".ico": "image/x-icon",
}

# PDFs with more pages than this require an explicit ``pages`` range, and a
# single read returns at most ``_PDF_MAX_PAGES_PER_READ`` pages.
_PDF_MAX_PAGES_WITHOUT_RANGE = 10
_PDF_MAX_PAGES_PER_READ = 20

# Image types accepted by the Anthropic, OpenAI, Gemini and DashScope APIs.
_DEFAULT_MODEL_INPUT_TYPES = [
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
]


class _ReadParams(ParamsBase):
    """The parameters of the Read tool."""

    file_path: str = Field(
        description="The absolute path to the file to read.",
    )
    offset: int = Field(
        default=1,
        ge=1,
        description="Optional 1-based line number to start reading from. "
        "Only applies to plain text files (default: 1)",
    )
    limit: int = Field(
        default=2000,
        ge=1,
        le=2000,
        description="Optional maximum number of lines to read. Only applies "
        "to plain text files (default: 2000, max: 2000)",
    )
    pages: str | None = Field(
        default=None,
        description='Page range for PDF files (e.g. "1-5", "3", "10-20"), '
        f"max {_PDF_MAX_PAGES_PER_READ} pages per request; required for "
        f"PDFs over {_PDF_MAX_PAGES_WITHOUT_RANGE} pages. Only applies to "
        "PDF files.",
    )


class Read(ToolBase):
    """The read tool."""

    name: str = "Read"
    """The tool name presented to the agent."""

    # pylint: disable=line-too-long
    _DESCRIPTION_HEAD: str = """Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters
- Results are returned using cat -n format, with line numbers starting at 1"""  # noqa: E501

    @property
    def description(self) -> str:  # type: ignore[override]
        """The description presented to the agent, with the image and PDF
        bullets rendered from the model's accepted input types."""
        lines = [self._DESCRIPTION_HEAD]
        image_types = [
            t for t in self.model_input_types if t.startswith("image/")
        ]
        if image_types:
            lines.append(
                f"- This tool allows you to read images ({', '.join(image_types)}). When reading an image file the contents are presented visually as you're a multimodal LLM.",  # noqa: E501
            )
        pdf_presentation = (
            "When reading a PDF file the pages are presented to you as a document."  # noqa: E501
            if any(
                fnmatch.fnmatch("application/pdf", t)
                for t in self.model_input_types
            )
            else "Text is extracted per page."
        )
        lines.append(
            f"- This tool can read PDF files (.pdf). {pdf_presentation} For large PDFs (more than {_PDF_MAX_PAGES_WITHOUT_RANGE} pages), you MUST provide the pages parameter to read specific pages (max {_PDF_MAX_PAGES_PER_READ} pages per request).",  # noqa: E501
        )
        return "\n".join(lines)

    input_schema: dict[str, Any] = _ReadParams.model_json_schema()

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = True

    def __init__(
        self,
        max_line_characters: int = 2000,
        model_input_types: list[str] | None = None,
        middlewares: List[ToolMiddlewareBase] | None = None,
        backend: BackendBase | None = None,
    ) -> None:
        """Initialize the read tool.

        Args:
            max_line_characters (`int`, defaults to 2000):
                The maximum number of characters to include for each line when
                reading files. Lines longer than this will be truncated with
                a "[truncated]" suffix. This prevents overwhelming the agent
                with excessively long lines while still providing useful
                content.
            model_input_types (`list[str] | None`, optional):
                The media types the downstream model accepts as input,
                aligned with the model card's ``input_types`` field so it
                can be passed through directly, e.g.
                ``Read(model_input_types=model_card.input_types)``. Glob
                patterns like ``"image/*"`` are accepted. Files whose media
                type is listed are returned as ``DataBlock`` for the model
                to consume natively; otherwise images return an error and
                PDFs fall back to local text extraction. Defaults to
                ``image/png``, ``image/jpeg``, ``image/gif`` and
                ``image/webp``.
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
            backend (`BackendBase | None`, optional):
                The sandbox backend to use for file I/O. When ``None``,
                a :class:`LocalBackend` is created.
        """
        from ._backend import LocalBackend

        super().__init__(middlewares=middlewares)
        self._max_line_characters = max_line_characters
        self.model_input_types = (
            model_input_types or _DEFAULT_MODEL_INPUT_TYPES
        )
        """The media types the model accepts as input, see ``__init__``."""
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
        pages: str | None = None,
        _agent_state: AgentState | None = None,
    ) -> ToolChunk:
        """Read a file and return content as appropriate block types.

        Dispatches to format-specific readers based on file extension:
        - PDF: text extraction via RAG parser
        - Image: base64-encoded DataBlock
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

        # Determine file type by extension. splitext on the basename works
        # for both posix and windows style backend paths.
        ext = os.path.splitext(self._backend.basename(file_path))[1].lower()

        if ext == ".pdf":
            return await self._read_pdf(file_path, pages)
        if ext in _IMAGE_EXTENSIONS:
            return await self._read_image_file(file_path, ext)

        return await self._read_text_file(
            file_path,
            offset,
            limit,
            _agent_state,
        )

    async def _read_image_file(
        self,
        file_path: str,
        ext: str,
    ) -> ToolChunk:
        """Read an image file and return as DataBlock."""
        media_type = _IMAGE_EXTENSIONS[ext]
        if not any(
            fnmatch.fnmatch(media_type, t) for t in self.model_input_types
        ):
            image_types = [
                t for t in self.model_input_types if t.startswith("image/")
            ]
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: Unsupported image type {media_type}, "
                        f"only {', '.join(image_types) or 'none'} are "
                        "supported.",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        try:
            raw = await self._backend.read_file(file_path)
        except Exception as e:
            return ToolChunk(
                content=[
                    TextBlock(text=f"Error reading file: {str(e)}"),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        return ToolChunk(
            content=[
                DataBlock(
                    source=Base64Source(
                        data=base64.b64encode(raw).decode("ascii"),
                        media_type=media_type,
                    ),
                    name=self._backend.basename(file_path),
                ),
            ],
            state=ToolResultState.RUNNING,
            is_last=True,
        )

    async def _read_pdf(
        self,
        file_path: str,
        pages: str | None = None,
    ) -> ToolChunk:
        """Read a PDF file. When the model accepts ``application/pdf`` the
        requested pages are returned as a PDF ``DataBlock``, otherwise their
        text is extracted locally into a ``TextBlock``."""
        import io

        from pypdf import PdfReader, PdfWriter

        try:
            raw = await self._backend.read_file(file_path)
            reader = PdfReader(io.BytesIO(raw))
            total_pages = len(reader.pages)
        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Error reading PDF: {str(e)}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        if pages is None:
            if total_pages > _PDF_MAX_PAGES_WITHOUT_RANGE:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=f"Error: PDF has {total_pages} pages, "
                            f"more than {_PDF_MAX_PAGES_WITHOUT_RANGE}. "
                            "You must provide the pages parameter (e.g. "
                            '"1-5") to read specific pages, max '
                            f"{_PDF_MAX_PAGES_PER_READ} pages per request.",
                        ),
                    ],
                    state=ToolResultState.ERROR,
                    is_last=True,
                )
            first, last = 1, total_pages
        else:
            match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", pages)
            if not match:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=f"Error: Invalid pages {pages!r}. Expected "
                            'a page number or range like "3" or "1-5".',
                        ),
                    ],
                    state=ToolResultState.ERROR,
                    is_last=True,
                )
            first = int(match.group(1))
            last = int(match.group(2)) if match.group(2) else first
            if first < 1 or first > last or first > total_pages:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=f"Error: Invalid pages {pages!r}. PDF has "
                            f"{total_pages} page(s).",
                        ),
                    ],
                    state=ToolResultState.ERROR,
                    is_last=True,
                )
            last = min(last, total_pages)
            if last - first + 1 > _PDF_MAX_PAGES_PER_READ:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=f"Error: Requested {last - first + 1} "
                            f"pages, at most {_PDF_MAX_PAGES_PER_READ} "
                            "pages can be read per request.",
                        ),
                    ],
                    state=ToolResultState.ERROR,
                    is_last=True,
                )

        if not any(
            fnmatch.fnmatch("application/pdf", t)
            for t in self.model_input_types
        ):
            text_parts = [
                f"--- Page {page_num}/{total_pages} ---\n"
                f"{reader.pages[page_num - 1].extract_text() or ''}"
                for page_num in range(first, last + 1)
            ]
            return ToolChunk(
                content=[TextBlock(text="\n\n".join(text_parts))],
                state=ToolResultState.RUNNING,
                is_last=True,
            )

        # Hand the PDF itself to the model, trimmed to the requested pages.
        if (first, last) != (1, total_pages):
            writer = PdfWriter()
            for page_num in range(first, last + 1):
                writer.add_page(reader.pages[page_num - 1])
            buf = io.BytesIO()
            writer.write(buf)
            raw = buf.getvalue()
        return ToolChunk(
            content=[
                DataBlock(
                    source=Base64Source(
                        data=base64.b64encode(raw).decode("ascii"),
                        media_type="application/pdf",
                    ),
                    name=self._backend.basename(file_path),
                ),
            ],
            state=ToolResultState.RUNNING,
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
