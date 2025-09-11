# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=line-too-long
"""The pandas read file tool in agentscope."""
import os
from typing import List, Any

import pandas as pd

from ._response import ToolResponse
from ..exception import ToolInvalidArgumentsError
from ..message import TextBlock


async def read_file_with_pandas(
    file_path: str,
    file_type: str = "auto",
    encoding: str = "utf-8",
    rows_limit: int | None = None,
    columns: List[str] | None = None,
    **kwargs: Any,
) -> ToolResponse:
    """Read file content using pandas and return structured data information.

    Args:
        file_path (`str`):
            The target file path.
        file_type (`str`, optional):
            File type, supports "auto", "csv", "excel", "json", "parquet".
            Defaults to "auto" (auto-detect from file extension).
        encoding (`str`, optional):
            File encoding. Defaults to "utf-8".
        rows_limit (`int`, optional):
            Maximum number of rows to display. If None, show all rows.
        columns (`List[str]`, optional):
            Specific columns to read. If None, read all columns.
        **kwargs:
            Additional arguments passed to pandas read functions.

    Returns:
        `ToolResponse`:
            The tool response containing file information and data preview or an error message.
    """
    # Check if file exists
    if not os.path.exists(file_path):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The file {file_path} does not exist.",
                ),
            ],
        )

    if not os.path.isfile(file_path):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The path {file_path} is not a file.",
                ),
            ],
        )

    try:
        # Auto-detect file type from extension
        if file_type == "auto":
            file_extension = os.path.splitext(file_path)[1].lower()
            if file_extension in [".csv"]:
                file_type = "csv"
            elif file_extension in [".xlsx", ".xls"]:
                file_type = "excel"
            elif file_extension in [".json"]:
                file_type = "json"
            elif file_extension in [".parquet"]:
                file_type = "parquet"
            else:
                file_type = "csv"  # Default to CSV

        # Read file based on type
        df = _read_file_by_type(
            file_path,
            file_type,
            encoding,
            columns,
            **kwargs,
        )

        # Apply row limit if specified
        if rows_limit is not None and rows_limit > 0:
            display_df = df.head(rows_limit)
            is_truncated = len(df) > rows_limit
        else:
            display_df = df
            is_truncated = False

        # Generate summary information
        file_info = _generate_file_summary(
            df,
            file_path,
            is_truncated,
            rows_limit,
        )

        # Generate data preview
        data_preview = _generate_data_preview(display_df)

        # Combine information
        content_text = f"{file_info}\n\n{data_preview}"

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=content_text,
                ),
            ],
        )

    except Exception as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error reading file {file_path}: {str(e)}",
                ),
            ],
        )


def _read_file_by_type(
    file_path: str,
    file_type: str,
    encoding: str,
    columns: List[str] | None = None,
    **kwargs: Any,
) -> pd.DataFrame:
    """Read file based on specified type."""

    # Common parameters
    common_params = {}
    if columns is not None:
        common_params["usecols"] = columns

    # Merge with user kwargs
    common_params.update(kwargs)

    if file_type == "csv":
        return pd.read_csv(file_path, encoding=encoding, **common_params)
    elif file_type == "excel":
        return pd.read_excel(file_path, **common_params)
    elif file_type == "json":
        return pd.read_json(file_path, encoding=encoding, **common_params)
    elif file_type == "parquet":
        return pd.read_parquet(file_path, **common_params)
    else:
        raise ToolInvalidArgumentsError(f"Unsupported file type: {file_type}")


def _generate_file_summary(
    df: pd.DataFrame,
    file_path: str,
    is_truncated: bool,
    rows_limit: int | None,
) -> str:
    """Generate file summary information."""

    summary_lines = [
        f"📁 File: {os.path.basename(file_path)}",
        f"📊 Shape: {df.shape[0]} rows × {df.shape[1]} columns",
        f"🏷️  Columns: {list(df.columns.tolist())}",
    ]

    # Add data types info
    dtype_info = df.dtypes.value_counts()
    dtype_summary = ", ".join(
        [f"{count} {dtype}" for dtype, count in dtype_info.items()],
    )
    summary_lines.append(f"🔢 Data Types: {dtype_summary}")

    # Add memory usage
    memory_usage = df.memory_usage(deep=True).sum()
    memory_mb = memory_usage / 1024 / 1024
    summary_lines.append(f"💾 Memory Usage: {memory_mb:.2f} MB")

    # Add truncation info
    if is_truncated:
        summary_lines.append(
            f"⚠️  Showing first {rows_limit} rows (file has {len(df)} total rows)",
        )

    return "\n".join(summary_lines)


def _generate_data_preview(df: pd.DataFrame) -> str:
    """Generate data preview."""

    preview_lines = ["📋 Data Preview:"]

    # Convert dataframe to string with better formatting
    df_str = df.to_string(max_rows=10, max_cols=10, show_dimensions=False)

    preview_lines.extend(
        [
            "```",
            df_str,
            "```",
        ],
    )

    # Add basic statistics for numeric columns
    numeric_cols = df.select_dtypes(include=["number"]).columns
    if len(numeric_cols) > 0:
        preview_lines.append("\n📈 Numeric Columns Summary:")
        stats_df = df[numeric_cols].describe()
        stats_str = stats_df.to_string()
        preview_lines.extend(
            [
                "```",
                stats_str,
                "```",
            ],
        )

    # Add missing values info
    missing_info = df.isnull().sum()
    missing_cols = missing_info[missing_info > 0]
    if len(missing_cols) > 0:
        preview_lines.append("\n⚠️  Missing Values:")
        for col, count in missing_cols.items():
            percentage = (count / len(df)) * 100
            preview_lines.append(f"  • {col}: {count} ({percentage:.1f}%)")

    return "\n".join(preview_lines)
