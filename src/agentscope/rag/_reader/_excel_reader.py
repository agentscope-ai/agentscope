# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The Excel reader to read and chunk Excel files."""
import base64
import hashlib
import json
from typing import Any, Literal

from ._reader_base import ReaderBase
from ._text_reader import TextReader
from ._utils import _get_media_type_from_data
from .._document import Document, DocMetadata
from ...message import ImageBlock, Base64Source, TextBlock
from ..._logging import logger


def _get_excel_column_name(col_index: int) -> str:
    """Convert a 0-based column index to Excel column name (A, B, ..., Z, AA,
    AB, ...).

    Args:
        col_index (`int`):
            The 0-based column index.

    Returns:
        `str`:
            The Excel column name (e.g., 'A' for 0, 'B' for 1, 'AA' for 26).
    """
    result = ""
    col_index += 1  # Convert to 1-based
    while col_index > 0:
        col_index -= 1
        result = chr(ord("A") + col_index % 26) + result
        col_index //= 26
    return result


def _extract_table_data(df: Any) -> list[list[str]]:
    """Extract table data from a DataFrame, handling NaN values.

    Args:
        df (`Any`):
            The pandas DataFrame object.

    Returns:
        `list[list[str]]`:
            Table data represented as a 2D list, where each inner list
            represents a row, and each string in the row represents a cell.
    """
    import pandas as pd

    table_data = []
    for _, row in df.iterrows():
        row_data = []
        for cell_val in row:
            # Convert NaN to empty string, preserve line breaks
            if pd.isna(cell_val):
                cell_text = ""
            else:
                cell_text = str(cell_val).strip()
                # Normalize line breaks
                cell_text = cell_text.replace("\r\n", "\n").replace("\r", "\n")
            row_data.append(cell_text)
        table_data.append(row_data)

    return table_data


def _extract_images_from_worksheet(
    worksheet: Any,
) -> list[tuple[int, ImageBlock]]:
    """Extract images from a worksheet with their row positions.

    Args:
        worksheet (`Any`):
            The openpyxl worksheet object.

    Returns:
        `list[tuple[int, ImageBlock]]`:
            A list of tuples containing (row_index, ImageBlock), where
            row_index is 0-based. Empty if no images found.
    """
    images = []

    if not (hasattr(worksheet, "_images") and worksheet._images):
        return images

    for img in worksheet._images:
        try:
            # Get image row position (0-based)
            row_index = 0
            if hasattr(img, "anchor") and hasattr(img.anchor, "_from"):
                row_index = img.anchor._from.row

            # Get image data
            img_data = img._data()

            # Determine media type
            media_type = _get_media_type_from_data(img_data)

            # Convert to base64
            base64_data = base64.b64encode(img_data).decode("utf-8")

            image_block = ImageBlock(
                type="image",
                source=Base64Source(
                    type="base64",
                    media_type=media_type,
                    data=base64_data,
                ),
            )

            images.append((row_index, image_block))
        except Exception as e:
            logger.warning("Failed to extract image from worksheet: %s", e)

    return images


class ExcelReader(ReaderBase):
    """The Excel reader that supports reading text, image, and table
    content from Excel files (.xlsx, .xls files), and chunking the text
    content into smaller pieces.

    .. note:: The table content can be extracted in Markdown or JSON format.

        **Markdown format example** (``include_cell_coordinates=False``):

        .. code-block:: text

            | Name  | Age | City     |
            |-------|-----|----------|
            | Alice | 25  | New York |
            | Bob   | 30  | London   |

        **Markdown format example** (``include_cell_coordinates=True``):

        .. code-block:: text

            | [A1] Name  | [B1] Age | [C1] City     |
            |------------|----------|---------------|
            | [A2] Alice | [B2] 25  | [C2] New York |
            | [A3] Bob   | [B3] 30  | [C3] London   |

        **JSON format example** (``include_cell_coordinates=False``):

        .. code-block:: json

            ["Name", "Age", "City"]
            ["Alice", "25", "New York"]
            ["Bob", "30", "London"]

        **JSON format example** (``include_cell_coordinates=True``):

        .. code-block:: json

            {"A1": "Name", "B1": "Age", "C1": "City"}
            {"A2": "Alice", "B2": "25", "C2": "New York"}
            {"A3": "Bob", "B3": "30", "C3": "London"}
    """

    def __init__(
        self,
        chunk_size: int = 512,
        split_by: Literal["char", "sentence", "paragraph"] = "sentence",
        include_sheet_names: bool = True,
        include_cell_coordinates: bool = False,
        include_image: bool = False,
        separate_sheet: bool = False,
        separate_table: bool = False,
        table_format: Literal["markdown", "json"] = "markdown",
    ) -> None:
        """Initialize the Excel reader.

        Args:
            chunk_size (`int`, default to 512):
                The size of each chunk, in number of characters.
            split_by (`Literal["char", "sentence", "paragraph"]`, default to \
            "sentence"):
                The unit to split the text, can be "char", "sentence", or
                "paragraph". The "sentence" option is implemented using the
                "nltk" library, which only supports English text.
            include_sheet_names (`bool`, default to True):
                Whether to include sheet names in the extracted text.
            include_cell_coordinates (`bool`, default to False):
                Whether to include cell coordinates (e.g., A1, B2) in the
                extracted text.
            include_image (`bool`, default to False):
                Whether to include image content in the document. If True,
                images will be extracted and included as base64-encoded images.
            separate_sheet (`bool`, default to False):
                Whether to treat each sheet as a separate document. If True,
                each sheet will be extracted as a separate Document object
                instead of being merged together.
            separate_table (`bool`, default to False):
                If True, tables will be treated as a new chunk to avoid
                truncation. But note when the table exceeds the chunk size,
                it will still be truncated.
            table_format (`Literal["markdown", "json"]`, \
            default to "markdown"):
                The format to extract table content. Note if the table cell
                contains `\n`, the Markdown format may not render correctly.
                In that case, you can use the `json` format, which extracts
                the table as a JSON string of a `list[list[str]]` object.
        """
        self._validate_init_params(chunk_size, split_by)

        if table_format not in ["markdown", "json"]:
            raise ValueError(
                "The table_format must be one of 'markdown' or 'json', "
                f"got {table_format}",
            )

        self.chunk_size = chunk_size
        self.split_by = split_by
        self.include_sheet_names = include_sheet_names
        self.include_cell_coordinates = include_cell_coordinates
        self.include_image = include_image
        self.separate_sheet = separate_sheet
        self.separate_table = separate_table
        self.table_format = table_format

        # Use TextReader to do the chunking
        self._text_reader = TextReader(self.chunk_size, self.split_by)

    def _validate_init_params(self, chunk_size: int, split_by: str) -> None:
        """Validate initialization parameters.

        Args:
            chunk_size (`int`):
                The chunk size to validate.
            split_by (`str`):
                The split mode to validate.
        """
        if chunk_size <= 0:
            raise ValueError(
                f"The chunk_size must be positive, got {chunk_size}",
            )

        if split_by not in ["char", "sentence", "paragraph"]:
            raise ValueError(
                "The split_by must be one of 'char', 'sentence' or "
                f"'paragraph', got {split_by}",
            )

    async def __call__(
        self,
        excel_path: str,
    ) -> list[Document]:
        """Read an Excel file, split it into chunks, and return a list of
        Document objects. The text, image, and table content will be returned
        in the same order as they appear in the Excel file.

        Args:
            excel_path (`str`):
                The input Excel file path (.xlsx or .xls file).

        Returns:
            `list[Document]`:
                A list of Document objects, where the metadata contains the
                chunked text, doc id and chunk id.
        """
        # Generate document ID
        doc_id = self.get_doc_id(excel_path)

        # Load Excel file and workbook
        excel_file = None
        workbook = None

        try:
            import pandas as pd
        except ImportError as e:
            raise ImportError(
                "Please install pandas to use the Excel reader. "
                "You can install it by `pip install pandas`.",
            ) from e

        try:
            excel_file = pd.ExcelFile(excel_path)

            # Load workbook if images are needed
            if self.include_image:
                try:
                    from openpyxl import load_workbook

                    workbook = load_workbook(excel_path)
                except ImportError:
                    logger.warning(
                        "openpyxl not available, image extraction disabled",
                    )
                    workbook = None

            # Process sheets
            if self.separate_sheet:
                return await self._process_sheets_separately(
                    excel_file,
                    doc_id,
                    workbook,
                )
            else:
                return await self._process_sheets_merged(
                    excel_file,
                    doc_id,
                    workbook,
                )

        except (
            pd.errors.EmptyDataError,
            pd.errors.ParserError,
            FileNotFoundError,
            PermissionError,
        ) as e:
            raise ValueError(
                f"Failed to read Excel file {excel_path}: {e}",
            ) from e
        finally:
            # Ensure all resources are closed
            if workbook is not None:
                workbook.close()
            if excel_file is not None:
                excel_file.close()

    async def _process_sheets_merged(
        self,
        excel_file: Any,
        doc_id: str,
        workbook: Any = None,
    ) -> list[Document]:
        """Process all sheets as a merged document, maintaining order of
        text, table, and image content.

        Args:
            excel_file (`Any`):
                The pandas ExcelFile object.
            doc_id (`str`):
                The document ID.
            workbook (`Any`, optional):
                The openpyxl workbook if available.

        Returns:
            `list[Document]`:
                A list of Document objects from all sheets merged together,
                maintaining content order.
        """
        # Get all blocks from all sheets in order
        all_blocks = []
        for sheet_name in excel_file.sheet_names:
            sheet_blocks = self._get_sheet_blocks(
                excel_file,
                sheet_name,
                workbook,
            )
            all_blocks.extend(sheet_blocks)

        # Convert blocks to documents
        return await self._blocks_to_documents(all_blocks, doc_id)

    async def _process_sheets_separately(
        self,
        excel_file: Any,
        doc_id: str,
        workbook: Any = None,
    ) -> list[Document]:
        """Process each sheet as separate documents.

        Args:
            excel_file (`Any`):
                The pandas ExcelFile object.
            doc_id (`str`):
                The document ID.
            workbook (`Any`, optional):
                The openpyxl workbook if available.

        Returns:
            `list[Document]`:
                A list of Document objects with each sheet processed
                separately.
        """
        all_docs = []

        for sheet_name in excel_file.sheet_names:
            sheet_blocks = self._get_sheet_blocks(
                excel_file,
                sheet_name,
                workbook,
            )
            sheet_docs = await self._blocks_to_documents(sheet_blocks, doc_id)
            all_docs.extend(sheet_docs)

        return all_docs

    def _get_sheet_blocks(
        self,
        excel_file: Any,
        sheet_name: str,
        workbook: Any = None,
    ) -> list[TextBlock | ImageBlock]:
        """Extract all data blocks from a sheet in order (text, table, image).

        Args:
            excel_file (`Any`):
                The pandas ExcelFile object.
            sheet_name (`str`):
                The name of the sheet.
            workbook (`Any`, optional):
                The openpyxl workbook if available.

        Returns:
            `list[TextBlock | ImageBlock]`:
                A list of data blocks extracted from the sheet, maintaining
                the order they appear in the sheet based on row positions.
        """
        blocks: list[TextBlock | ImageBlock] = []
        positioned_blocks: list[tuple[int, TextBlock | ImageBlock, str]] = []

        # Add sheet header
        sheet_header = (
            f"Sheet: {sheet_name}" if self.include_sheet_names else None
        )

        try:
            df = excel_file.parse(sheet_name=sheet_name)

            if df.empty:
                return blocks

            # Extract images with their row positions if enabled
            images_with_positions: list[tuple[int, ImageBlock]] = []
            if self.include_image and workbook is not None:
                try:
                    worksheet = workbook[sheet_name]
                    images_with_positions = _extract_images_from_worksheet(
                        worksheet,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to extract images from sheet '%s': %s",
                        sheet_name,
                        e,
                    )

            # Extract table data
            table_data = _extract_table_data(df)

            if self.table_format == "markdown":
                table_text = self._table_to_markdown(table_data, sheet_header)
            else:
                table_text = self._table_to_json(table_data, sheet_header)

            # Calculate table row position for sorting
            # Row 0 is the header row in pandas (if header exists)
            # Table data spans from row 0 to row len(df)
            # In Excel, this is typically row 1 to row (len(df) + 1) in
            # 1-based indexing
            # In 0-based indexing used by openpyxl: row 0 to row len(df)
            table_start_row = 0

            # Create table block
            table_block = TextBlock(
                type="text",
                text=table_text,
            )

            # Add table block with its position for sorting
            positioned_blocks.append((table_start_row, table_block, "table"))

            # Add image blocks with their positions
            for row_index, image_block in images_with_positions:
                positioned_blocks.append((row_index, image_block, "image"))

            # Sort blocks by row position
            positioned_blocks.sort(key=lambda x: x[0])

            # Extract blocks in sorted order and merge consecutive blocks
            # if needed
            last_type = None
            for row_index, block, block_type in positioned_blocks:
                if block_type == "table":
                    # Handle table block merging based on separate_table
                    # Logic matches WordReader: merge if not separate_table and
                    # last_type is "text" or "table"
                    if not self.separate_table and last_type in [
                        "text",
                        "table",
                    ]:
                        blocks[-1]["text"] += "\n" + block["text"]
                    else:
                        blocks.append(block)
                    last_type = "table"
                elif block_type == "image":
                    blocks.append(block)
                    last_type = "image"

        except Exception as e:
            logger.warning("Failed to process sheet '%s': %s", sheet_name, e)

        return blocks

    async def _blocks_to_documents(
        self,
        blocks: list[TextBlock | ImageBlock],
        doc_id: str,
    ) -> list[Document]:
        """Convert data blocks to Document objects.

        Args:
            blocks (`list[TextBlock | ImageBlock]`):
                A list of data blocks.
            doc_id (`str`):
                The document ID.

        Returns:
            `list[Document]`:
                A list of Document objects.
        """
        documents = []

        for block in blocks:
            if block["type"] == "text":
                # Process text blocks through TextReader for chunking
                text_docs = await self._text_reader(block["text"])
                for doc in text_docs:
                    # Update doc_id but keep other metadata
                    doc.metadata.doc_id = doc_id
                    doc.id = doc_id
                    documents.append(doc)
            elif block["type"] == "image":
                # Images are independent documents
                documents.append(
                    Document(
                        metadata=DocMetadata(
                            content=block,
                            doc_id=doc_id,
                            chunk_id=0,  # Will be set later
                            total_chunks=1,
                        ),
                    ),
                )

        # Set chunk ids and total chunks
        total_chunks = len(documents)
        for idx, doc in enumerate(documents):
            doc.metadata.chunk_id = idx
            doc.metadata.total_chunks = total_chunks

        return documents

    def _table_to_markdown(
        self,
        table_data: list[list[str]],
        sheet_header: str | None = None,
    ) -> str:
        """Convert table data to Markdown format.

        Args:
            table_data (`list[list[str]]`):
                Table data represented as a 2D list.
            sheet_header (`str | None`, optional):
                Optional sheet header to prepend.

        Returns:
            `str`:
                Table in Markdown format.
        """
        if not table_data:
            return sheet_header or ""

        md_table = ""

        # Add sheet header if provided
        if sheet_header:
            md_table += sheet_header + "\n"

        # If no rows, return header only
        if not table_data or not table_data[0]:
            return md_table.strip() or ""

        num_cols = len(table_data[0])

        # Escape pipe characters in cells to avoid breaking Markdown table
        # structure
        def escape_pipes(cell_text: str) -> str:
            """Escape pipe characters in cell content."""
            return cell_text.replace("|", "\\|")

        def format_cell(cell: str, row_idx: int, col_idx: int) -> str:
            """Format cell content with optional coordinates."""
            escaped = escape_pipes(cell)
            if self.include_cell_coordinates:
                coord = f"{_get_excel_column_name(col_idx)}{row_idx + 1}"
                return f"[{coord}] {escaped}"
            return escaped

        # Header row (first row)
        escaped_header = [
            format_cell(cell, 0, col_idx)
            for col_idx, cell in enumerate(table_data[0])
        ]
        header_row = "| " + " | ".join(escaped_header) + " |\n"
        md_table += header_row

        # Separator row
        separator_row = "| " + " | ".join(["---"] * num_cols) + " |\n"
        md_table += separator_row

        # Data rows
        for row_idx, row in enumerate(table_data[1:], start=1):
            # Ensure row has same number of columns as header
            while len(row) < num_cols:
                row.append("")
            # Format each cell with optional coordinates
            formatted_row = [
                format_cell(cell, row_idx, col_idx)
                for col_idx, cell in enumerate(row[:num_cols])
            ]
            data_row = "| " + " | ".join(formatted_row) + " |\n"
            md_table += data_row

        return md_table

    def _table_to_json(
        self,
        table_data: list[list[str]],
        sheet_header: str | None = None,
    ) -> str:
        """Convert table data to JSON string.

        Args:
            table_data (`list[list[str]]`):
                Table data represented as a 2D list.
            sheet_header (`str | None`, optional):
                Optional sheet header to prepend.

        Returns:
            `str`:
                Table in JSON string format.
        """
        json_strs = []

        # Add sheet header if provided
        if sheet_header:
            json_strs.append(sheet_header)

        # Add system info marker
        json_strs.append(
            "<system-info>A table loaded as a JSON array:</system-info>",
        )

        for row_idx, row in enumerate(table_data):
            if self.include_cell_coordinates:
                # Include cell coordinates in the format {"A1": "value", ...}
                row_dict = {
                    f"{_get_excel_column_name(col_idx)}{row_idx + 1}": cell
                    for col_idx, cell in enumerate(row)
                }
                json_strs.append(json.dumps(row_dict, ensure_ascii=False))
            else:
                json_strs.append(json.dumps(row, ensure_ascii=False))

        return "\n".join(json_strs)

    def get_doc_id(self, excel_path: str) -> str:
        """Generate unique document ID from file path.

        Args:
            excel_path (`str`):
                The path to the Excel file.

        Returns:
            `str`:
                The document ID (SHA256 hash of the file path).
        """
        return hashlib.sha256(excel_path.encode("utf-8")).hexdigest()
