# -*- coding: utf-8 -*-
"""Tests for the grep and glob search tools."""
import asyncio
import os
import tempfile
import shutil
import unittest

from agentscope.tool import grep_search, glob_search


class TestGrepSearch(unittest.TestCase):
    """Test cases for the grep_search tool."""

    def setUp(self) -> None:
        """Set up a temporary directory with test files."""
        self.test_dir = tempfile.mkdtemp()

        # Create test files
        self._write_file(
            "main.py",
            "import os\nimport sys\n\ndef main():\n"
            "    print('hello world')\n\nif __name__ == '__main__':\n"
            "    main()\n",
        )
        self._write_file(
            "utils.py",
            "def helper():\n    return 42\n\n"
            "def another_helper():\n    return 'hello'\n",
        )
        self._write_file(
            "config.json",
            '{"name": "test", "version": "1.0"}\n',
        )
        os.makedirs(os.path.join(self.test_dir, "sub"), exist_ok=True)
        self._write_file(
            "sub/nested.py",
            "# A nested file\nclass MyClass:\n"
            "    def method(self):\n        pass\n",
        )
        # Create a hidden directory with a file (should be skipped)
        os.makedirs(os.path.join(self.test_dir, ".hidden"), exist_ok=True)
        self._write_file(
            ".hidden/secret.py",
            "SECRET_KEY = 'should_not_match'\n",
        )

    def tearDown(self) -> None:
        """Clean up the temporary directory."""
        shutil.rmtree(self.test_dir)

    def _write_file(self, rel_path: str, content: str) -> None:
        """Helper to write a file in the test directory."""
        filepath = os.path.join(self.test_dir, rel_path)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

    def _run(self, coro):
        """Helper to run an async function."""
        return asyncio.run(coro)

    def test_basic_search(self) -> None:
        """Test searching for a simple pattern."""
        result = self._run(
            grep_search(pattern="hello", directory=self.test_dir),
        )
        text = result.content[0]["text"]
        self.assertIn("hello", text)
        self.assertIn("main.py", text)

    def test_regex_pattern(self) -> None:
        """Test searching with a regex pattern."""
        result = self._run(
            grep_search(pattern=r"def \w+\(", directory=self.test_dir),
        )
        text = result.content[0]["text"]
        self.assertIn("def main(", text)
        self.assertIn("def helper(", text)

    def test_case_insensitive(self) -> None:
        """Test case-insensitive search."""
        result = self._run(
            grep_search(
                pattern="HELLO",
                directory=self.test_dir,
                case_sensitive=False,
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("hello", text)

    def test_include_filter(self) -> None:
        """Test filtering files by extension."""
        result = self._run(
            grep_search(
                pattern="test",
                directory=self.test_dir,
                include="*.json",
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("config.json", text)
        self.assertNotIn("main.py", text)

    def test_no_matches(self) -> None:
        """Test when no matches are found."""
        result = self._run(
            grep_search(
                pattern="nonexistent_pattern_xyz",
                directory=self.test_dir,
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("No matches found", text)

    def test_invalid_directory(self) -> None:
        """Test with a non-existent directory."""
        result = self._run(
            grep_search(
                pattern="test",
                directory="/nonexistent/path",
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("Error", text)
        self.assertIn("does not exist", text)

    def test_invalid_regex(self) -> None:
        """Test with an invalid regex pattern."""
        result = self._run(
            grep_search(
                pattern="[invalid",
                directory=self.test_dir,
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("Error", text)
        self.assertIn("Invalid regex", text)

    def test_context_lines(self) -> None:
        """Test displaying context lines around matches."""
        result = self._run(
            grep_search(
                pattern="def main",
                directory=self.test_dir,
                context_lines=1,
            ),
        )
        text = result.content[0]["text"]
        # Should show context lines before and after the match
        self.assertIn("def main():", text)
        self.assertIn("print", text)
        # The line before (line 3) and line after (line 5) should be present
        self.assertIn("3:", text)
        self.assertIn("5:", text)

    def test_max_results(self) -> None:
        """Test result truncation with max_results."""
        result = self._run(
            grep_search(
                pattern="def|import|return|class|pass",
                directory=self.test_dir,
                max_results=3,
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("truncated", text.lower())

    def test_nested_files(self) -> None:
        """Test searching in nested directories."""
        result = self._run(
            grep_search(
                pattern="MyClass",
                directory=self.test_dir,
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("sub/nested.py", text.replace("\\", "/"))

    def test_skips_hidden_directories(self) -> None:
        """Test that hidden directories are skipped."""
        result = self._run(
            grep_search(
                pattern="SECRET_KEY",
                directory=self.test_dir,
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("No matches found", text)

    def test_not_a_directory(self) -> None:
        """Test with a file path instead of directory."""
        filepath = os.path.join(self.test_dir, "main.py")
        result = self._run(
            grep_search(
                pattern="test",
                directory=filepath,
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("Error", text)
        self.assertIn("not a directory", text)


class TestGlobSearch(unittest.TestCase):
    """Test cases for the glob_search tool."""

    def setUp(self) -> None:
        """Set up a temporary directory with test files."""
        self.test_dir = tempfile.mkdtemp()

        # Create test files
        for name in ["main.py", "utils.py", "config.json", "readme.md"]:
            filepath = os.path.join(self.test_dir, name)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# {name}\n")

        # Create nested structure
        sub_dir = os.path.join(self.test_dir, "src", "core")
        os.makedirs(sub_dir, exist_ok=True)
        for name in ["app.py", "models.py", "styles.css"]:
            filepath = os.path.join(sub_dir, name)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# {name}\n")

    def tearDown(self) -> None:
        """Clean up the temporary directory."""
        shutil.rmtree(self.test_dir)

    def _run(self, coro):
        """Helper to run an async function."""
        return asyncio.run(coro)

    def test_basic_glob(self) -> None:
        """Test finding files with a simple pattern."""
        result = self._run(
            glob_search(pattern="*.py", directory=self.test_dir),
        )
        text = result.content[0]["text"]
        self.assertIn("main.py", text)
        self.assertIn("utils.py", text)
        self.assertNotIn("config.json", text)

    def test_recursive_glob(self) -> None:
        """Test recursive file finding with **."""
        result = self._run(
            glob_search(pattern="**/*.py", directory=self.test_dir),
        )
        text = result.content[0]["text"]
        self.assertIn("main.py", text)
        self.assertIn("app.py", text)
        self.assertIn("models.py", text)

    def test_specific_extension(self) -> None:
        """Test finding files with a specific extension."""
        result = self._run(
            glob_search(pattern="*.json", directory=self.test_dir),
        )
        text = result.content[0]["text"]
        self.assertIn("config.json", text)
        self.assertNotIn(".py", text)

    def test_no_matches(self) -> None:
        """Test when no files match."""
        result = self._run(
            glob_search(pattern="*.xyz", directory=self.test_dir),
        )
        text = result.content[0]["text"]
        self.assertIn("No files found", text)

    def test_invalid_directory(self) -> None:
        """Test with a non-existent directory."""
        result = self._run(
            glob_search(
                pattern="*.py",
                directory="/nonexistent/path",
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("Error", text)
        self.assertIn("does not exist", text)

    def test_max_results(self) -> None:
        """Test result truncation."""
        result = self._run(
            glob_search(
                pattern="**/*",
                directory=self.test_dir,
                max_results=2,
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("Showing 2", text)

    def test_file_sizes_shown(self) -> None:
        """Test that file sizes are displayed."""
        result = self._run(
            glob_search(pattern="*.py", directory=self.test_dir),
        )
        text = result.content[0]["text"]
        # File sizes should be shown (e.g. "10B" or "1.2KB")
        self.assertTrue(
            "B)" in text or "KB)" in text,
            f"Expected file size in output: {text}",
        )

    def test_not_a_directory(self) -> None:
        """Test with a file path instead of directory."""
        filepath = os.path.join(self.test_dir, "main.py")
        result = self._run(
            glob_search(pattern="*.py", directory=filepath),
        )
        text = result.content[0]["text"]
        self.assertIn("Error", text)
        self.assertIn("not a directory", text)

    def test_nested_pattern(self) -> None:
        """Test finding files in a specific subdirectory."""
        result = self._run(
            glob_search(
                pattern="src/**/*.py",
                directory=self.test_dir,
            ),
        )
        text = result.content[0]["text"]
        self.assertIn("app.py", text)
        self.assertIn("models.py", text)
        self.assertNotIn("main.py", text)


if __name__ == "__main__":
    unittest.main()
