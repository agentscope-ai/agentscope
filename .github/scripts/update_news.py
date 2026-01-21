#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script to automatically update NEWS section in README files.
Reads the first 10 news items from docs/NEWS.md and updates README.md and
README_zh.md.
"""

from pathlib import Path


def read_news_items(news_file: Path, max_items: int = 10) -> list[str]:
    """
    Read news items from NEWS.md file.

    Args:
        news_file (`Path`):
            Path to the NEWS.md file
        max_items (`int`, optional):
            Maximum number of items to read

    Returns:
        `list[str]`:
            List of news items
    """
    with open(news_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Split by lines that start with "- **["
    lines = content.strip().split("\n")
    news_items = []

    for line in lines:
        if line.strip().startswith("- **["):
            news_items.append(line)
            if len(news_items) >= max_items:
                break

    return news_items


def update_readme(
    readme_file: Path,
    news_items: list[str],
) -> None:
    """
    Update the NEWS section in README file using HTML comment markers.

    Args:
        readme_file (`Path`):
            Path to the README file
        news_items (`list[str]`):
            List of news items to insert
    """
    with open(readme_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Use HTML comment markers to identify the NEWS section
    begin_marker = "<!-- BEGIN NEWS -->"
    end_marker = "<!-- END NEWS -->"

    if begin_marker not in content or end_marker not in content:
        print(f"⚠️  NEWS markers not found in {readme_file.name}")
        print(
            f"    Please add '{begin_marker}' and '{end_marker}' to mark the "
            f"NEWS section",
        )
        return

    # Find positions of markers
    begin_pos = content.find(begin_marker)
    end_pos = content.find(end_marker)

    if begin_pos == -1 or end_pos == -1 or begin_pos >= end_pos:
        print(f"❌ Invalid NEWS markers in {readme_file.name}")
        return

    # Create new NEWS content
    news_content = "\n".join(news_items)

    # Replace content between markers
    new_content = (
        content[: begin_pos + len(begin_marker)]
        + "\n"
        + news_content
        + "\n"
        + content[end_pos:]
    )

    with open(readme_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✅ Updated {readme_file.name}")


def main() -> None:
    """Main function to update NEWS in README files."""
    # Define paths
    repo_root = Path(__file__).parent.parent.parent
    news_file_en = repo_root / "docs" / "NEWS.md"
    news_file_zh = repo_root / "docs" / "NEWS_zh.md"
    readme_en = repo_root / "README.md"
    readme_zh = repo_root / "README_zh.md"

    # Update English README from NEWS.md
    if news_file_en.exists():
        print(f"📖 Reading news items from {news_file_en}")
        news_items_en = read_news_items(news_file_en, max_items=10)
        print(f"📰 Found {len(news_items_en)} English news items")

        if news_items_en and readme_en.exists():
            print(f"📝 Updating {readme_en.name}...")
            update_readme(readme_en, news_items_en)
        elif not news_items_en:
            print("⚠️  No English news items found")
        else:
            print(f"⚠️  {readme_en} not found")
    else:
        print(f"❌ NEWS.md not found at {news_file_en}")

    # Update Chinese README from NEWS_zh.md
    if news_file_zh.exists() and news_file_zh.stat().st_size > 0:
        print(f"📖 Reading news items from {news_file_zh}")
        news_items_zh = read_news_items(news_file_zh, max_items=10)
        print(f"📰 Found {len(news_items_zh)} Chinese news items")

        if news_items_zh and readme_zh.exists():
            print(f"📝 Updating {readme_zh.name}...")
            update_readme(readme_zh, news_items_zh)
        elif not news_items_zh:
            print("⚠️  No Chinese news items found")
        else:
            print(f"⚠️  {readme_zh} not found")
    else:
        print(
            f"⚠️  NEWS_zh.md not found or empty at {news_file_zh}, "
            f"using English news for Chinese README",
        )
        # Fallback: use English news for Chinese README if NEWS_zh.md
        # doesn't exist
        if news_file_en.exists() and readme_zh.exists():
            print(f"📖 Reading news items from {news_file_en} (fallback)")
            news_items = read_news_items(news_file_en, max_items=10)
            if news_items:
                print(f"📝 Updating {readme_zh.name} with English news...")
                update_readme(readme_zh, news_items)

    print("✨ All done!")


if __name__ == "__main__":
    main()
