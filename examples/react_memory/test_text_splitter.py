# -*- coding: utf-8 -*-
"""Test examples for the recursive text splitter."""

import re
import logging
from typing import (
    Callable,
    List,
    Literal,
    Optional,
    Union,
)


def _split_text_with_regex(
    text: str,
    separator: str,
    keep_separator: Union[bool, Literal["start", "end"]],
) -> List[str]:
    """Split text with regex separator."""
    # Now that we have the separator, split the text
    if separator:
        if keep_separator:
            # The parentheses in the pattern keep the delimiters in the result.
            _splits = re.split(f"({separator})", text)
            splits = (
                (
                    [
                        _splits[i] + _splits[i + 1]
                        for i in range(0, len(_splits) - 1, 2)
                    ]
                )
                if keep_separator == "end"
                else (
                    [
                        _splits[i] + _splits[i + 1]
                        for i in range(1, len(_splits), 2)
                    ]
                )
            )
            if len(_splits) % 2 == 0:
                splits += _splits[-1:]
            splits = (
                (splits + [_splits[-1]])
                if keep_separator == "end"
                else ([_splits[0]] + splits)
            )
        else:
            splits = re.split(separator, text)
    else:
        splits = list(text)
    return [s for s in splits if s != ""]


def _merge_splits(
    splits: List[str],
    separator: str,
    chunk_size: int,
    chunk_overlap: int,
    length_function: Callable[[str], int],
) -> List[str]:
    """Merge splits into chunks with overlap."""
    separator_len = length_function(separator)

    docs = []
    current_doc: List[str] = []
    total = 0
    for d in splits:
        _len = length_function(d)
        if (
            total + _len + (separator_len if len(current_doc) > 0 else 0)
            > chunk_size
        ):
            if total > chunk_size:
                logging.warning(
                    "Created a chunk of size %s, "
                    "which is larger than the specified %s",
                    total,
                    chunk_size,
                )
            if current_doc:
                doc = separator.join(current_doc).strip()
                if doc:
                    docs.append(doc)
                # Keep on popping if:
                # - we have a larger chunk than in the chunk overlap
                # - or if we still have any chunks and the length is long
                while total > chunk_overlap or (
                    total
                    + _len
                    + (separator_len if len(current_doc) > 0 else 0)
                    > chunk_size
                    and total > 0
                ):
                    total -= length_function(current_doc[0]) + (
                        separator_len if len(current_doc) > 1 else 0
                    )
                    current_doc.pop(0)
        current_doc.append(d)
        total += _len + (separator_len if len(current_doc) > 1 else 0)
    doc = separator.join(current_doc).strip()
    if doc:
        docs.append(doc)
    return docs


def create_recursive_text_splitter(
    chunk_size: int = 4000,
    chunk_overlap: int = 200,
    length_function: Optional[Callable[[str], int]] = None,
    separators: Optional[List[str]] = None,
    keep_separator: Union[bool, Literal["start", "end"]] = True,
    is_separator_regex: bool = False,
) -> Callable[[str], List[str]]:
    """Create a recursive text splitter function.

    Returns a function that splits text into chunks based on the
    provided configuration.

    Args:
        chunk_size: Maximum size of each chunk
        chunk_overlap: Overlap size between chunks
        length_function: Function to calculate text length (default: len)
        separators: List of separators to try
            (default: ["\n\n", "\n", " ", ""])
        keep_separator: Whether to keep separators in the output
        is_separator_regex: Whether separators are regex patterns

    Returns:
        A function that takes text and returns a list of text chunks
    """
    length_function = length_function or len
    separators = separators or ["\n\n", "\n", " ", ""]

    def _split_text(text: str, separators: List[str]) -> List[str]:
        """Split incoming text and return chunks."""
        final_chunks = []
        # Get appropriate separator to use
        separator = separators[-1]
        new_separators = []
        for i, _s in enumerate(separators):
            _separator = _s if is_separator_regex else re.escape(_s)
            if _s == "":
                separator = _s
                break
            if re.search(_separator, text):
                separator = _s
                new_separators = separators[i + 1 :]
                break

        _separator = separator if is_separator_regex else re.escape(separator)
        splits = _split_text_with_regex(text, _separator, keep_separator)

        # Now go merging things, recursively splitting longer texts.
        _good_splits = []
        _separator = "" if keep_separator else separator
        for s in splits:
            if length_function(s) < chunk_size:
                _good_splits.append(s)
            else:
                if _good_splits:
                    merged_text = _merge_splits(
                        _good_splits,
                        _separator,
                        chunk_size,
                        chunk_overlap,
                        length_function,
                    )
                    final_chunks.extend(merged_text)
                    _good_splits = []
                if not new_separators:
                    final_chunks.append(s)
                else:
                    other_info = _split_text(s, new_separators)
                    final_chunks.extend(other_info)
        if _good_splits:
            merged_text = _merge_splits(
                _good_splits,
                _separator,
                chunk_size,
                chunk_overlap,
                length_function,
            )
            final_chunks.extend(merged_text)
        return final_chunks

    def split_text(text: str) -> List[str]:
        """Split text into chunks."""
        return _split_text(text, separators)

    return split_text


def test_basic_split() -> None:
    """Test basic text splitting functionality."""
    print("=" * 60)
    print("Test 1: Basic Text Splitting")
    print("=" * 60)

    splitter = create_recursive_text_splitter(
        chunk_size=100,
        chunk_overlap=20,
    )

    text = """
    This is the first paragraph.

    This is the second paragraph.

    This is the third paragraph.
    """

    chunks = splitter(text)
    print(f"Original text length: {len(text)}")
    print(f"Number of chunks: {len(chunks)}")
    print("\nChunks:")
    for i, chunk in enumerate(chunks, 1):
        print(f"\n--- Chunk {i} (length: {len(chunk)}) ---")
        print(chunk)


def test_custom_separators() -> None:
    """Test with custom separators."""
    print("\n" + "=" * 60)
    print("Test 2: Custom Separators")
    print("=" * 60)

    splitter = create_recursive_text_splitter(
        chunk_size=50,
        chunk_overlap=10,
        separators=["\n\n", "\n", ". ", " "],
    )

    text = """First section.

Second section.

Third section with multiple sentences. This is another sentence.
And one more."""

    chunks = splitter(text)
    print(f"Original text length: {len(text)}")
    print(f"Number of chunks: {len(chunks)}")
    print("\nChunks:")
    for i, chunk in enumerate(chunks, 1):
        print(f"\n--- Chunk {i} (length: {len(chunk)}) ---")
        print(chunk)


def test_long_text() -> None:
    """Test splitting long text."""
    print("\n" + "=" * 60)
    print("Test 3: Long Text Splitting")
    print("=" * 60)

    splitter = create_recursive_text_splitter(
        chunk_size=200,
        chunk_overlap=50,
    )

    # Generate a long text
    paragraphs = []
    for i in range(10):
        paragraphs.append(f"This is paragraph {i+1}. " * 20)
    text = "\n\n".join(paragraphs)

    chunks = splitter(text)
    print(f"Original text length: {len(text)}")
    print(f"Number of chunks: {len(chunks)}")
    print("\nFirst 3 chunks:")
    for i, chunk in enumerate(chunks[:3], 1):
        print(f"\n--- Chunk {i} (length: {len(chunk)}) ---")
        print(chunk[:100] + "..." if len(chunk) > 100 else chunk)
    print(f"\n... and {len(chunks) - 3} more chunks")


def test_keep_separator() -> None:
    """Test keep_separator option."""
    print("\n" + "=" * 60)
    print("Test 4: Keep Separator Options")
    print("=" * 60)

    text = "Section 1\n\nSection 2\n\nSection 3"

    # Test with keep_separator=True (default)
    splitter_keep = create_recursive_text_splitter(
        chunk_size=50,
        chunk_overlap=10,
        keep_separator=True,
    )
    chunks_keep = splitter_keep(text)

    # Test with keep_separator=False
    splitter_no_keep = create_recursive_text_splitter(
        chunk_size=50,
        chunk_overlap=10,
        keep_separator=False,
    )
    chunks_no_keep = splitter_no_keep(text)

    print("Original text:")
    print(repr(text))
    print("\nWith keep_separator=True:")
    for i, chunk in enumerate(chunks_keep, 1):
        print(f"Chunk {i}: {repr(chunk)}")
    print("\nWith keep_separator=False:")
    for i, chunk in enumerate(chunks_no_keep, 1):
        print(f"Chunk {i}: {repr(chunk)}")


def test_custom_length_function() -> None:
    """Test with custom length function (e.g., token counting)."""
    print("\n" + "=" * 60)
    print("Test 5: Custom Length Function")
    print("=" * 60)

    # Simple token counter (count words)
    def word_count(text: str) -> int:
        return len(text.split())

    splitter = create_recursive_text_splitter(
        chunk_size=20,  # 20 words per chunk
        chunk_overlap=5,  # 5 words overlap
        length_function=word_count,
    )

    text = """
    This is a test document with multiple sentences.
    Each sentence contains several words.
    We want to split this text based on word count rather than character count.
    This allows for more semantic chunking.
    """

    chunks = splitter(text)
    print(f"Original text word count: {word_count(text)}")
    print(f"Number of chunks: {len(chunks)}")
    print("\nChunks:")
    for i, chunk in enumerate(chunks, 1):
        word_cnt = word_count(chunk)
        print(f"\n--- Chunk {i} (words: {word_cnt}) ---")
        print(chunk)


def test_regex_separators() -> None:
    """Test with regex separators."""
    print("\n" + "=" * 60)
    print("Test 6: Regex Separators")
    print("=" * 60)

    splitter = create_recursive_text_splitter(
        chunk_size=100,
        chunk_overlap=20,
        separators=[
            r"\n\n+",
            r"\n",
            r"\. ",
            " ",
        ],  # Multiple newlines, single newline, period+space, space
        is_separator_regex=True,
    )

    text = """First section.


Second section.

Third section. With sentences. And more text."""

    chunks = splitter(text)
    print(f"Original text length: {len(text)}")
    print(f"Number of chunks: {len(chunks)}")
    print("\nChunks:")
    for i, chunk in enumerate(chunks, 1):
        print(f"\n--- Chunk {i} (length: {len(chunk)}) ---")
        print(repr(chunk))


def test_chinese_text() -> None:
    """Test with Chinese text."""
    print("\n" + "=" * 60)
    print("Test 7: Chinese Text")
    print("=" * 60)

    splitter = create_recursive_text_splitter(
        chunk_size=100,
        chunk_overlap=20,
        separators=["\n\n", "\n", "。", "，", " "],
    )

    text = """
    这是第一段文字。包含多个句子，用于测试文本分割功能。

    这是第二段文字。同样包含多个句子，用于验证分割器的工作效果。

    这是第三段文字。最后一段用于完成测试。
    """

    chunks = splitter(text)
    print(f"Original text length: {len(text)}")
    print(f"Number of chunks: {len(chunks)}")
    print("\nChunks:")
    for i, chunk in enumerate(chunks, 1):
        print(f"\n--- Chunk {i} (length: {len(chunk)}) ---")
        print(chunk)


def test_edge_cases() -> None:
    """Test edge cases."""
    print("\n" + "=" * 60)
    print("Test 8: Edge Cases")
    print("=" * 60)

    splitter = create_recursive_text_splitter(
        chunk_size=50,
        chunk_overlap=10,
    )

    # Test empty text
    print("1. Empty text:")
    chunks = splitter("")
    print(f"   Result: {chunks}")

    # Test text shorter than chunk_size
    print("\n2. Text shorter than chunk_size:")
    text = "Short text"
    chunks = splitter(text)
    print(f"   Original: {repr(text)}")
    print(f"   Chunks: {chunks}")

    # Test text with no separators
    print("\n3. Text with no separators (single long word):")
    text = "a" * 200
    chunks = splitter(text)
    print(f"   Original length: {len(text)}")
    print(f"   Number of chunks: {len(chunks)}")
    print(f"   First chunk length: {len(chunks[0]) if chunks else 0}")


if __name__ == "__main__":
    # Run all tests
    test_basic_split()
    test_custom_separators()
    test_long_text()
    test_keep_separator()
    test_custom_length_function()
    test_regex_separators()
    test_chinese_text()
    test_edge_cases()

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)
