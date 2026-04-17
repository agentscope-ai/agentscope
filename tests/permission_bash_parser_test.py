# -*- coding: utf-8 -*-
"""Test cases for BashCommandParser."""
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool._permission._bash_parser import BashCommandParser


class BashCommandParserTest(IsolatedAsyncioTestCase):
    """Test cases for BashCommandParser."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.parser = BashCommandParser()

    async def test_single_command_with_prefix(self) -> None:
        """Test single commands that can extract prefixes."""
        test_cases = [
            ("git commit -m 'fix'", ["git commit"]),
            ("npm run build", ["npm run"]),
            ("docker compose up", ["docker compose"]),
            ("cargo build --release", ["cargo build"]),
            ("python -m pytest", ["python -m"]),
        ]

        for command, expected in test_cases:
            with self.subTest(command=command):
                result = self.parser.extract_command_prefixes(command)
                self.assertEqual(result, expected)

    async def test_single_command_without_prefix(self) -> None:
        """Test single commands that cannot extract prefixes."""
        test_cases = [
            ("ls", []),
            ("ls -la", []),
            ("echo hello", []),
            ("cd /tmp", []),
            ("pwd", []),
            ("cat file.txt", []),
        ]

        for command, expected in test_cases:
            with self.subTest(command=command):
                result = self.parser.extract_command_prefixes(command)
                self.assertEqual(result, expected)

    async def test_environment_variables_safe(self) -> None:
        """Test commands with safe environment variables."""
        test_cases = [
            ("NODE_ENV=prod npm run build", ["npm run"]),
            ("DEBUG=1 npm test", ["npm test"]),
            ("PATH=/usr/bin npm run build", ["npm run"]),
            ("CI=true npm run test", ["npm run"]),
            ("PYTHONUNBUFFERED=1 python -m pytest", ["python -m"]),
        ]

        for command, expected in test_cases:
            with self.subTest(command=command):
                result = self.parser.extract_command_prefixes(command)
                self.assertEqual(result, expected)

    async def test_environment_variables_unsafe(self) -> None:
        """Test commands with unsafe environment variables."""
        test_cases = [
            ("CUSTOM_VAR=value npm run build", []),
            ("MY_SECRET=123 npm test", []),
            ("API_KEY=abc npm run deploy", []),
        ]

        for command, expected in test_cases:
            with self.subTest(command=command):
                result = self.parser.extract_command_prefixes(command)
                self.assertEqual(result, expected)

    async def test_compound_command_and_operator(self) -> None:
        """Test compound commands with && operator."""
        test_cases = [
            ("git add . && git commit", ["git add", "git commit"]),
            (
                "npm install && npm run build && npm test",
                ["npm install", "npm run", "npm test"],
            ),
            ("docker build . && docker push", ["docker build", "docker push"]),
        ]

        for command, expected in test_cases:
            with self.subTest(command=command):
                result = self.parser.extract_command_prefixes(command)
                self.assertEqual(result, expected)

    async def test_compound_command_or_operator(self) -> None:
        """Test compound commands with || operator."""
        test_cases = [
            ("npm run build || echo failed", ["npm run"]),
            ("git commit || git status", ["git commit", "git status"]),
        ]

        for command, expected in test_cases:
            with self.subTest(command=command):
                result = self.parser.extract_command_prefixes(command)
                self.assertEqual(result, expected)

    async def test_compound_command_semicolon(self) -> None:
        """Test compound commands with ; operator."""
        test_cases = [
            ("cd /tmp; ls -la; pwd", []),
            ("npm install; npm run build", ["npm install", "npm run"]),
        ]

        for command, expected in test_cases:
            with self.subTest(command=command):
                result = self.parser.extract_command_prefixes(command)
                self.assertEqual(result, expected)

    async def test_compound_command_pipe(self) -> None:
        """Test compound commands with | operator."""
        test_cases = [
            ("cat file.txt | grep error", []),
            ("docker ps | grep nginx", ["docker ps"]),
            ("npm run build | tee output.log", ["npm run"]),
        ]

        for command, expected in test_cases:
            with self.subTest(command=command):
                result = self.parser.extract_command_prefixes(command)
                self.assertEqual(result, expected)

    async def test_compound_command_mixed(self) -> None:
        """Test compound commands with mixed operators."""
        test_cases = [
            (
                "git add . && git commit -m 'fix' || echo failed",
                ["git add", "git commit"],
            ),
            (
                "npm install && npm run build | tee log.txt",
                ["npm install", "npm run"],
            ),
        ]

        for command, expected in test_cases:
            with self.subTest(command=command):
                result = self.parser.extract_command_prefixes(command)
                self.assertEqual(result, expected)

    async def test_edge_cases(self) -> None:
        """Test edge cases."""
        test_cases = [
            ("", []),
            ("   ", []),
            ("npm", []),
            ("   npm run build   ", ["npm run"]),
        ]

        for command, expected in test_cases:
            with self.subTest(command=command):
                result = self.parser.extract_command_prefixes(command)
                self.assertEqual(result, expected)

    async def test_max_prefixes_limit(self) -> None:
        """Test that max_prefixes parameter limits the results."""
        command = (
            "npm install && npm run build && npm test && "
            "npm run lint && npm run format && npm run deploy"
        )

        # Default max is 5, but with deduplication we get 3 unique prefixes
        result = self.parser.extract_command_prefixes(command)
        self.assertEqual(len(result), 3)
        self.assertEqual(
            result,
            ["npm install", "npm run", "npm test"],
        )

        # Custom max
        result = self.parser.extract_command_prefixes(command, max_prefixes=3)
        self.assertEqual(len(result), 3)

    async def test_deduplication(self) -> None:
        """Test that duplicate prefixes are removed."""
        test_cases = [
            (
                "npm run build && npm run test && npm run lint",
                ["npm run"],
            ),
            (
                "git add . && git commit && git push && git status",
                ["git add", "git commit", "git push", "git status"],
            ),
        ]

        for command, expected in test_cases:
            with self.subTest(command=command):
                result = self.parser.extract_command_prefixes(command)
                self.assertEqual(result, expected)

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.parser = None


class BashParserReadOnlyTest(IsolatedAsyncioTestCase):
    """Test is_read_only_command() method."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.parser = BashCommandParser()

    async def test_single_read_only_git_commands(self) -> None:
        """Test single read-only git commands."""
        read_only_commands = [
            "git status",
            "git log",
            "git diff",
            "git show",
            "git branch",
            "git remote -v",
            "git log --oneline",
        ]
        for cmd in read_only_commands:
            with self.subTest(cmd=cmd):
                self.assertTrue(
                    self.parser.is_read_only_command(cmd),
                    f"Expected '{cmd}' to be read-only",
                )

    async def test_single_read_only_file_commands(self) -> None:
        """Test single read-only file commands."""
        read_only_commands = [
            "ls",
            "ls -la",
            "cat file.txt",
            "head -n 10 file.txt",
            "tail -f log.txt",
            "grep pattern file.txt",
            "find . -name '*.py'",
            "tree",
            "pwd",
            "which python",
        ]
        for cmd in read_only_commands:
            with self.subTest(cmd=cmd):
                self.assertTrue(
                    self.parser.is_read_only_command(cmd),
                    f"Expected '{cmd}' to be read-only",
                )

    async def test_single_read_only_docker_commands(self) -> None:
        """Test single read-only docker commands."""
        read_only_commands = [
            "docker ps",
            "docker images",
            "docker inspect container_id",
            "docker logs container_id",
        ]
        for cmd in read_only_commands:
            with self.subTest(cmd=cmd):
                self.assertTrue(
                    self.parser.is_read_only_command(cmd),
                    f"Expected '{cmd}' to be read-only",
                )

    async def test_single_non_read_only_commands(self) -> None:
        """Test single non-read-only commands."""
        non_read_only_commands = [
            "git commit -m 'message'",
            "git push",
            "git pull",
            "rm file.txt",
            "mv file1.txt file2.txt",
            "cp file1.txt file2.txt",
            "chmod +x script.sh",
            "mkdir new_dir",
            "touch file.txt",
        ]
        for cmd in non_read_only_commands:
            with self.subTest(cmd=cmd):
                self.assertFalse(
                    self.parser.is_read_only_command(cmd),
                    f"Expected '{cmd}' to be non-read-only",
                )

    async def test_compound_command_all_read_only(self) -> None:
        """Test compound commands with all read-only subcommands."""
        compound_commands = [
            "ls -la && cat file.txt",
            "git status && git log",
            "pwd && ls",
            "cat file1.txt || cat file2.txt",
            "ls; pwd; cat file.txt",
            "git diff | grep pattern",
        ]
        for cmd in compound_commands:
            with self.subTest(cmd=cmd):
                self.assertTrue(
                    self.parser.is_read_only_command(cmd),
                    f"Expected '{cmd}' to be read-only "
                    f"(all subcommands are read-only)",
                )

    async def test_compound_command_mixed(self) -> None:
        """Test compound commands with mixed read-only and non-read-only."""
        mixed_commands = [
            "ls -la && git commit -m 'message'",
            "cat file.txt && rm file.txt",
            "git status && git push",
            "pwd || mkdir new_dir",
            "ls; touch file.txt",
        ]
        for cmd in mixed_commands:
            with self.subTest(cmd=cmd):
                self.assertFalse(
                    self.parser.is_read_only_command(cmd),
                    f"Expected '{cmd}' to be non-read-only "
                    f"(contains non-read-only subcommand)",
                )

    async def test_commands_with_output_redirection(self) -> None:
        """Test commands with output redirections are not read-only."""
        redirect_commands = [
            "cat file.txt > output.txt",
            "ls -la > list.txt",
            "git log >> history.txt",
            "echo 'hello' > file.txt",
            "cat file.txt 2> error.log",
            "ls &> output.log",
        ]
        for cmd in redirect_commands:
            with self.subTest(cmd=cmd):
                self.assertFalse(
                    self.parser.is_read_only_command(cmd),
                    f"Expected '{cmd}' to be non-read-only "
                    f"(contains output redirection)",
                )

    async def test_commands_with_dangerous_paths(self) -> None:
        """Test commands with dangerous paths."""
        # Note: dangerous path check is separate from read-only check
        # These commands are still considered read-only if they don't
        # modify files
        dangerous_read_only = [
            "cat ~/.bashrc",
            "ls ~/.ssh",
            "cat .git/config",
        ]
        for cmd in dangerous_read_only:
            with self.subTest(cmd=cmd):
                self.assertTrue(
                    self.parser.is_read_only_command(cmd),
                    f"Expected '{cmd}' to be read-only "
                    f"(dangerous path doesn't affect read-only status)",
                )

        # These are non-read-only because they modify files
        dangerous_non_read_only = [
            "rm ~/.bashrc",
            "chmod 600 ~/.ssh/config",
            "mv file.txt ~/.ssh/",
        ]
        for cmd in dangerous_non_read_only:
            with self.subTest(cmd=cmd):
                self.assertFalse(
                    self.parser.is_read_only_command(cmd),
                    f"Expected '{cmd}' to be non-read-only "
                    f"(modifies files)",
                )

    async def test_empty_and_whitespace_commands(self) -> None:
        """Test empty and whitespace-only commands."""
        empty_commands = [
            "",
            "   ",
            "\t",
            "\n",
        ]
        for cmd in empty_commands:
            with self.subTest(cmd=repr(cmd)):
                # Empty commands return False (not read-only, but
                # also not executable)
                self.assertFalse(
                    self.parser.is_read_only_command(cmd),
                    "Expected empty/whitespace command to return False",
                )

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.parser = None


class BashParserFilePathsTest(IsolatedAsyncioTestCase):
    """Test extract_file_paths() method."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.parser = BashCommandParser()

    async def test_rm_command(self) -> None:
        """Test file path extraction from rm commands."""
        test_cases = [
            ("rm file.txt", [("rm", "file.txt")]),
            ("rm -rf /tmp/test", [("rm", "/tmp/test")]),
            (
                "rm file1.txt file2.txt",
                [("rm", "file1.txt"), ("rm", "file2.txt")],
            ),
            ("rm -f *.log", [("rm", "*.log")]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_mv_command(self) -> None:
        """Test file path extraction from mv commands."""
        test_cases = [
            ("mv old.txt new.txt", [("mv", "old.txt"), ("mv", "new.txt")]),
            (
                "mv /tmp/file.txt /home/user/",
                [("mv", "/tmp/file.txt"), ("mv", "/home/user/")],
            ),
            (
                "mv -f file1.txt file2.txt",
                [("mv", "file1.txt"), ("mv", "file2.txt")],
            ),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_cp_command(self) -> None:
        """Test file path extraction from cp commands."""
        test_cases = [
            (
                "cp file.txt backup.txt",
                [("cp", "file.txt"), ("cp", "backup.txt")],
            ),
            ("cp -r /src /dest", [("cp", "/src"), ("cp", "/dest")]),
            (
                "cp file1.txt file2.txt /tmp/",
                [("cp", "file1.txt"), ("cp", "file2.txt"), ("cp", "/tmp/")],
            ),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_chmod_command(self) -> None:
        """Test file path extraction from chmod commands."""
        test_cases = [
            ("chmod +x script.sh", [("chmod", "+x"), ("chmod", "script.sh")]),
            ("chmod 755 /usr/bin/tool", [("chmod", "/usr/bin/tool")]),
            ("chmod -R 644 /tmp/files", [("chmod", "/tmp/files")]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_chown_command(self) -> None:
        """Test file path extraction from chown commands."""
        test_cases = [
            (
                "chown user:group file.txt",
                [("chown", "user:group"), ("chown", "file.txt")],
            ),
            (
                "chown -R user /var/www",
                [("chown", "user"), ("chown", "/var/www")],
            ),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_touch_command(self) -> None:
        """Test file path extraction from touch commands."""
        test_cases = [
            ("touch file.txt", [("touch", "file.txt")]),
            (
                "touch file1.txt file2.txt",
                [("touch", "file1.txt"), ("touch", "file2.txt")],
            ),
            ("touch /tmp/newfile.log", [("touch", "/tmp/newfile.log")]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_ln_command(self) -> None:
        """Test file path extraction from ln commands."""
        test_cases = [
            ("ln -s target link", [("ln", "target"), ("ln", "link")]),
            (
                "ln /src/file /dest/file",
                [("ln", "/src/file"), ("ln", "/dest/file")],
            ),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_sed_command(self) -> None:
        """Test file path extraction from sed commands."""
        test_cases = [
            ("sed -i 's/old/new/' file.txt", [("sed", "file.txt")]),
            ("sed 's/pattern/replacement/' input.txt", [("sed", "input.txt")]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_output_redirections(self) -> None:
        """Test file path extraction from output redirections."""
        test_cases = [
            (
                "echo 'hello' > output.txt",
                [("redirect", "output.txt")],
            ),
            ("cat file.txt > backup.txt", [("redirect", "backup.txt")]),
            ("ls -la >> list.txt", [("redirect", "list.txt")]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_compound_commands(self) -> None:
        """Test file path extraction from compound commands."""
        test_cases = [
            (
                "rm file1.txt && rm file2.txt",
                [("rm", "file1.txt"), ("rm", "file2.txt")],
            ),
            (
                "touch new.txt && chmod +x new.txt",
                [("touch", "new.txt"), ("chmod", "+x"), ("chmod", "new.txt")],
            ),
            (
                "cp src.txt dest.txt || mv src.txt dest.txt",
                [
                    ("cp", "src.txt"),
                    ("cp", "dest.txt"),
                    ("mv", "src.txt"),
                    ("mv", "dest.txt"),
                ],
            ),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_quoted_paths(self) -> None:
        """Test file path extraction with quoted paths."""
        test_cases = [
            ('rm "file with spaces.txt"', []),  # Quoted paths not extracted
            ("rm 'file.txt'", []),  # Quoted paths not extracted
            (
                'mv "old file.txt" "new file.txt"',
                [],  # Quoted paths not extracted
            ),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_dangerous_paths(self) -> None:
        """Test file path extraction with dangerous paths."""
        test_cases = [
            ("rm ~/.bashrc", [("rm", "~/.bashrc")]),
            ("chmod 600 ~/.ssh/config", [("chmod", "~/.ssh/config")]),
            (
                "mv file.txt .git/hooks/",
                [("mv", "file.txt"), ("mv", ".git/hooks/")],
            ),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_commands_without_file_operations(self) -> None:
        """Test commands that don't operate on files."""
        test_cases = [
            ("ls", []),
            ("pwd", []),
            ("echo 'hello'", []),
            ("git status", []),
            ("docker ps", []),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_file_paths(cmd)
                self.assertEqual(result, expected)

    async def test_empty_command(self) -> None:
        """Test empty command."""
        result = self.parser.extract_file_paths("")
        self.assertEqual(result, [])

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.parser = None


class BashParserRedirectionsTest(IsolatedAsyncioTestCase):
    """Test extract_redirections() method."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.parser = BashCommandParser()

    async def test_simple_output_redirection(self) -> None:
        """Test simple output redirection (>)."""
        test_cases = [
            ("echo 'hello' > output.txt", ["output.txt"]),
            ("cat file.txt > backup.txt", ["backup.txt"]),
            ("ls -la > list.txt", ["list.txt"]),
            ("git log > history.txt", ["history.txt"]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_redirections(cmd)
                self.assertEqual(result, expected)

    async def test_append_redirection(self) -> None:
        """Test append redirection (>>)."""
        test_cases = [
            ("echo 'line' >> log.txt", ["log.txt"]),
            ("cat file.txt >> combined.txt", ["combined.txt"]),
            ("ls >> list.txt", ["list.txt"]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_redirections(cmd)
                self.assertEqual(result, expected)

    async def test_error_redirection(self) -> None:
        """Test error redirection (2>)."""
        test_cases = [
            ("command 2> error.log", ["error.log"]),
            ("python script.py 2> stderr.txt", ["stderr.txt"]),
            ("npm run build 2> build_errors.log", ["build_errors.log"]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_redirections(cmd)
                self.assertEqual(result, expected)

    async def test_combined_redirection(self) -> None:
        """Test combined stdout/stderr redirection (&>)."""
        test_cases = [
            ("command &> output.log", ["output.log"]),
            ("python script.py &> all_output.txt", ["all_output.txt"]),
            ("npm test &> test_results.log", ["test_results.log"]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_redirections(cmd)
                self.assertEqual(result, expected)

    async def test_multiple_redirections(self) -> None:
        """Test commands with multiple redirections."""
        test_cases = [
            ("command > output.txt 2> error.log", ["output.txt", "error.log"]),
            (
                "python script.py > stdout.txt 2> stderr.txt",
                ["stdout.txt", "stderr.txt"],
            ),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_redirections(cmd)
                self.assertEqual(result, expected)

    async def test_compound_commands_with_redirections(self) -> None:
        """Test compound commands with multiple redirections."""
        test_cases = [
            (
                "echo 'a' > file1.txt && echo 'b' > file2.txt",
                ["file1.txt", "file2.txt"],
            ),
            (
                "cat file.txt > backup.txt || cp file.txt backup.txt",
                ["backup.txt"],
            ),
            ("ls > list1.txt; pwd > list2.txt", ["list1.txt", "list2.txt"]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_redirections(cmd)
                self.assertEqual(result, expected)

    async def test_redirections_with_quoted_paths(self) -> None:
        """Test redirections with quoted file paths."""
        test_cases = [
            (
                'echo "hello" > "output file.txt"',
                [],
            ),  # Quoted redirections not extracted
            (
                "cat file.txt > 'backup.txt'",
                [],
            ),  # Quoted redirections not extracted
            (
                'ls > "file with spaces.log"',
                [],
            ),  # Quoted redirections not extracted
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_redirections(cmd)
                self.assertEqual(result, expected)

    async def test_redirections_to_dangerous_paths(self) -> None:
        """Test redirections to dangerous paths."""
        test_cases = [
            ("echo 'alias' >> ~/.bashrc", ["~/.bashrc"]),
            ("cat key > ~/.ssh/authorized_keys", ["~/.ssh/authorized_keys"]),
            ("echo 'config' > .git/config", [".git/config"]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_redirections(cmd)
                self.assertEqual(result, expected)

    async def test_redirections_with_absolute_paths(self) -> None:
        """Test redirections with absolute paths."""
        test_cases = [
            ("echo 'data' > /tmp/output.txt", ["/tmp/output.txt"]),
            ("cat file.txt > /var/log/app.log", ["/var/log/app.log"]),
            ("ls > /home/user/list.txt", ["/home/user/list.txt"]),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_redirections(cmd)
                self.assertEqual(result, expected)

    async def test_commands_without_redirections(self) -> None:
        """Test commands without redirections."""
        test_cases = [
            ("ls -la", []),
            ("cat file.txt", []),
            ("echo 'hello'", []),
            ("git status", []),
            ("rm file.txt", []),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_redirections(cmd)
                self.assertEqual(result, expected)

    async def test_pipe_not_redirection(self) -> None:
        """Test that pipes (|) are not treated as redirections."""
        test_cases = [
            ("cat file.txt | grep pattern", []),
            ("ls | wc -l", []),
            ("git log | head -n 10", []),
        ]
        for cmd, expected in test_cases:
            with self.subTest(cmd=cmd):
                result = self.parser.extract_redirections(cmd)
                self.assertEqual(result, expected)

    async def test_empty_command(self) -> None:
        """Test empty command."""
        result = self.parser.extract_redirections("")
        self.assertEqual(result, [])

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.parser = None
