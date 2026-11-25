# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Host git isolation test case — real git, real ``LocalBackend``.

``GET /workspace/status`` runs git in whatever directory a session
points at. On the host that is code execution for anyone who can write
the repository's ``.git/config``: ``core.fsmonitor`` runs on every
index refresh and a filter driver runs for any path ``.gitattributes``
routes to it. The isolation must stop all of it without changing what
the status badge says.

Every attack below is first shown to fire through the plain ``git``
argv — the control that proves the trap is real — and then shown not
to fire through :meth:`WorkspaceService._read_git`. Fidelity cases
compare the isolated summary with the plain one on benign repositories,
field by field.

Needs a ``git`` binary; skipped without one.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase, skipIf, skipUnless
from unittest.mock import patch

from agentscope.app._service import WorkspaceService
from agentscope.app._service import _git_isolation, _workspace
from agentscope.app._service._git_isolation import isolated_git_argv
from agentscope.tool import ExecResult, LocalBackend

_HAS_GIT = shutil.which("git") is not None

#: Writes its first argument as a file name into the sentinel directory,
#: then passes stdin through — so it serves as a filter, a textconv and
#: an fsmonitor hook alike without breaking the command that ran it.
_TRAP_SOURCE = """\
import pathlib, sys
pathlib.Path(sys.argv[1], sys.argv[2]).touch()
if sys.argv[2] in ("filter", "textconv", "pager"):
    sys.stdout.write(sys.stdin.read())
"""


def _service() -> WorkspaceService:
    """A service whose git path needs none of its dependencies."""
    return WorkspaceService(None, None, "secret")  # type: ignore[arg-type]


class _RecordingBackend:
    """A non-local backend that records argv and answers like git."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Record the call; answer as a clean ``main`` branch."""
        _ = cwd, timeout
        self.calls.append(command)
        if "status" in command:
            return ExecResult(
                exit_code=0,
                stdout=b"# branch.oid abc\0# branch.head main\0",
                stderr=b"",
            )
        return ExecResult(exit_code=0, stdout=b"", stderr=b"")


@skipUnless(_HAS_GIT, "git binary not available")
class _GitTestBase(IsolatedAsyncioTestCase):
    """A scratch area, a trap script, and helpers to build repos."""

    def setUp(self) -> None:
        """Isolate from the tester's own git config and temp dir."""
        # Closed by the addCleanup below, not by a with block.
        # pylint: disable-next=consider-using-with
        self._scratch = tempfile.TemporaryDirectory()
        self.root = Path(self._scratch.name)
        self.sentinels = self.root / "sentinels"
        self.sentinels.mkdir()
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        home = self.root / "home"
        home.mkdir()

        self.trap = self.root / "trap.py"
        self.trap.write_text(_TRAP_SOURCE, encoding="utf-8")

        env_patch = patch.dict(
            os.environ,
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.com",
                "TMPDIR": str(self.tmp),
                "TEMP": str(self.tmp),
                "TMP": str(self.tmp),
            },
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        tempfile.tempdir = None
        self.addCleanup(setattr, tempfile, "tempdir", None)
        self.addCleanup(self._scratch.cleanup)

        self.backend = LocalBackend()
        self.service = _service()
        _git_isolation._RENDERED_CONFIG_CACHE.clear()
        self.addCleanup(_git_isolation._RENDERED_CONFIG_CACHE.clear)

    # ── building repositories ──────────────────────────────────────────

    def git(self, cwd: Path, *args: str) -> str:
        """Run git for setup, failing the test on error."""
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            self.fail(
                f"git {' '.join(args)} failed: "
                f"{result.stderr.decode(errors='replace')}",
            )
        return result.stdout.decode("utf-8", errors="replace")

    def make_repo(self, name: str = "repo", *extra: str) -> Path:
        """A repository with one commit of two files on ``main``."""
        repo = self.root / name
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main", *extra)
        (repo / "a.txt").write_text("1\n2\n3\n", encoding="utf-8")
        (repo / "b.py").write_text("x = 1\n", encoding="utf-8")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", "init")
        return repo

    def set_config(self, path: Path, key: str, value: str) -> None:
        """Write one entry into a config file, escaped by git itself."""
        self.git(self.root, "config", "--file", str(path), key, value)

    def trap_command(self, label: str) -> str:
        """A shell command line that trips the ``label`` sentinel."""
        return f'"{sys.executable}" "{self.trap}" "{self.sentinels}" {label}'

    def tripped(self) -> set[str]:
        """Names of the sentinels written so far."""
        return {path.name for path in self.sentinels.iterdir()}

    def clear_sentinels(self) -> None:
        """Forget every trip, between the control and the real run."""
        for path in self.sentinels.iterdir():
            path.unlink()

    # ── running the code under test ────────────────────────────────────

    async def plain(self, cwd: Path) -> Any:
        """The unisolated summary: what ran before this fix."""
        return await self.service._summarise_git(
            self.backend,
            str(cwd),
            _workspace._GIT_PLAIN,
        )

    async def isolated(self, cwd: Path) -> Any:
        """The summary the endpoint now computes for a host backend."""
        return await self.service._read_git(self.backend, str(cwd))

    async def assert_trap_blocked(
        self,
        cwd: Path,
        label: str,
    ) -> Any:
        """Show ``label`` fires unisolated, then stays silent isolated.

        Returns:
            `Any`: The isolated summary, for further assertions.
        """
        await self.plain(cwd)
        self.assertIn(
            label,
            self.tripped(),
            f"control: the {label} trap did not fire through plain git, "
            "so this case proves nothing",
        )
        self.clear_sentinels()

        summary = await self.isolated(cwd)
        self.assertEqual(self.tripped(), set(), "isolated git ran a trap")
        return summary


class SandboxBackendUnchangedTest(IsolatedAsyncioTestCase):
    """Non-host backends must see exactly the argv they saw before."""

    async def test_plain_argv_is_byte_for_byte_the_old_one(self) -> None:
        """``git`` plus the original arguments, nothing inserted."""
        backend = _RecordingBackend()

        await _service()._read_git(backend, "/workspace")  # type: ignore

        self.assertEqual(
            backend.calls,
            [
                [
                    "git",
                    "--no-optional-locks",
                    "status",
                    "--porcelain=v2",
                    "--branch",
                    "-z",
                    "--untracked-files=normal",
                    "--ignore-submodules=all",
                ],
                ["git", "--no-optional-locks", "diff", "--shortstat", "HEAD"],
            ],
        )


class HostGitAttacksTest(_GitTestBase):
    """Each way a repository can make git run a program, blocked."""

    async def test_fsmonitor(self) -> None:
        """``core.fsmonitor`` runs on every index refresh."""
        repo = self.make_repo()
        self.git(
            repo,
            "config",
            "core.fsmonitor",
            f"{self.trap_command('fsmonitor')}; false",
        )
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")

        summary = await self.assert_trap_blocked(repo, "fsmonitor")

        self.assertIsNotNone(summary)
        self.assertEqual(summary.branch, "main")

    async def test_clean_filter_routed_by_gitattributes(self) -> None:
        """A filter driver runs for any path routed to it.

        The driver's name is chosen by the repository, which is why no
        list of ``-c`` overrides could ever have covered this.
        """
        repo = self.make_repo()
        self.git(
            repo,
            "config",
            "filter.anything.clean",
            self.trap_command("filter"),
        )
        (repo / ".gitattributes").write_text(
            "*.txt filter=anything\n",
            encoding="utf-8",
        )
        (repo / "a.txt").write_text("1\n2\n3\n4\n", encoding="utf-8")

        summary = await self.assert_trap_blocked(repo, "filter")

        self.assertIsNotNone(summary)
        self.assertEqual(summary.insertions, 1)

    async def test_filter_routed_by_info_attributes(self) -> None:
        """Same, routed from ``.git/info/attributes`` instead."""
        repo = self.make_repo()
        self.git(
            repo,
            "config",
            "filter.hidden.clean",
            self.trap_command("filter"),
        )
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text(
            "*.txt filter=hidden\n",
            encoding="utf-8",
        )
        # A different size, so ``diff`` must filter the content to count
        # lines however the stat comparison goes.
        (repo / "a.txt").write_text("other content\n", encoding="utf-8")

        await self.assert_trap_blocked(repo, "filter")

    async def test_include_path(self) -> None:
        """``include.path`` pulls a second file in as config."""
        repo = self.make_repo()
        included = self.root / "included.cfg"
        self.set_config(
            included,
            "core.fsmonitor",
            f"{self.trap_command('fsmonitor')}; false",
        )
        self.git(repo, "config", "include.path", str(included))
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")

        await self.assert_trap_blocked(repo, "fsmonitor")

    async def test_attack_from_a_subdirectory(self) -> None:
        """Discovery walks up; the trap must not fire from below."""
        repo = self.make_repo()
        self.git(
            repo,
            "config",
            "core.fsmonitor",
            f"{self.trap_command('fsmonitor')}; false",
        )
        sub = repo / "deep" / "er"
        sub.mkdir(parents=True)
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")

        summary = await self.assert_trap_blocked(sub, "fsmonitor")

        self.assertIsNotNone(summary)

    async def test_linked_worktree(self) -> None:
        """A linked worktree reads config from the common directory."""
        repo = self.make_repo()
        self.git(repo, "branch", "side")
        linked = self.root / "linked"
        self.git(repo, "worktree", "add", "-q", str(linked), "side")
        self.git(
            repo,
            "config",
            "core.fsmonitor",
            f"{self.trap_command('fsmonitor')}; false",
        )
        (linked / "a.txt").write_text("changed\n", encoding="utf-8")

        summary = await self.assert_trap_blocked(linked, "fsmonitor")

        self.assertIsNotNone(summary)
        self.assertEqual(summary.branch, "side")

    async def test_embedded_bare_repository_is_not_entered(self) -> None:
        """A bare repo committed as files cannot stand in for the tree.

        Plain git under the default ``safe.bareRepository=all`` treats
        ``vendor/evil.git`` itself as the repository. The isolated walk
        skips it and reports the checkout around it.
        """
        repo = self.make_repo()
        bare = repo / "vendor" / "evil.git"
        bare.parent.mkdir()
        self.git(repo, "init", "-q", "--bare", str(bare))
        self.git(
            bare,
            "config",
            "core.fsmonitor",
            f"{self.trap_command('fsmonitor')}; false",
        )

        summary = await self.isolated(bare)

        self.assertEqual(self.tripped(), set())
        self.assertIsNotNone(summary)
        self.assertEqual(summary.branch, "main")

    async def test_config_rewritten_while_status_runs(self) -> None:
        """No window between a check and a use: nothing loads config.

        A thread toggles the real config between benign and hostile as
        fast as it can while the endpoint is polled repeatedly. A design
        that inspected the file and then let git load it would lose this
        race sooner or later.
        """
        repo = self.make_repo()
        config = repo / ".git" / "config"
        benign = config.read_bytes()
        scratch = self.root / "hostile.cfg"
        scratch.write_bytes(benign)
        self.set_config(
            scratch,
            "core.fsmonitor",
            f"{self.trap_command('fsmonitor')}; false",
        )
        hostile = scratch.read_bytes()
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")

        stop = threading.Event()

        def flip() -> None:
            while not stop.is_set():
                config.write_bytes(hostile)
                config.write_bytes(benign)

        flipper = threading.Thread(target=flip, daemon=True)
        flipper.start()
        try:
            for _ in range(25):
                await self.isolated(repo)
        finally:
            stop.set()
            flipper.join()

        self.assertEqual(self.tripped(), set())

    async def test_users_own_global_fsmonitor_is_not_run(self) -> None:
        """The monitor is switched off for the shadow, wherever set.

        A global monitor is the user's own, so this is about
        determinism, not trust: its token would be checked against an
        index that was copied rather than refreshed.
        """
        repo = self.make_repo()
        self.set_config(
            Path(os.environ["HOME"]) / ".gitconfig",
            "core.fsmonitor",
            f"{self.trap_command('fsmonitor')}; false",
        )
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")

        await self.assert_trap_blocked(repo, "fsmonitor")


@skipIf(os.name == "nt", "FIFOs and /dev/zero are POSIX-only")
class HostGitHostileFilesTest(_GitTestBase):
    """Git state replaced by files that would block or never end."""

    async def assert_refused(self, repo: Path) -> None:
        """No summary, no hang, no trap: the mirror is abandoned."""
        self.assertIsNone(await self.isolated(repo))
        self.assertEqual(self.tripped(), set())

    async def test_fifo_as_packed_refs(self) -> None:
        """Opening a FIFO for read would wait for a writer forever."""
        repo = self.make_repo()
        os.mkfifo(repo / ".git" / "packed-refs")

        await self.assert_refused(repo)

    async def test_fifo_as_head(self) -> None:
        """Same, for the one file every repository must have."""
        repo = self.make_repo()
        (repo / ".git" / "HEAD").unlink()
        os.mkfifo(repo / ".git" / "HEAD")

        await self.assert_refused(repo)

    async def test_fifo_among_refs(self) -> None:
        """Same, planted where a branch ref would be."""
        repo = self.make_repo()
        os.mkfifo(repo / ".git" / "refs" / "heads" / "trap")

        await self.assert_refused(repo)

    async def test_endless_index(self) -> None:
        """An index symlinked to ``/dev/zero`` must not fill the disk."""
        repo = self.make_repo()
        (repo / ".git" / "index").unlink()
        os.symlink("/dev/zero", repo / ".git" / "index")

        await self.assert_refused(repo)

    async def test_fifo_as_config(self) -> None:
        """Rejected before git is ever asked to parse it."""
        repo = self.make_repo()
        (repo / ".git" / "config").unlink()
        os.mkfifo(repo / ".git" / "config")

        await self.assert_refused(repo)


class HostGitFidelityTest(_GitTestBase):
    """On benign repositories the badge reads exactly as before."""

    async def assert_same_as_plain(self, cwd: Path) -> Any:
        """Isolated and plain summaries agree on every field."""
        plain = await self.plain(cwd)
        isolated = await self.isolated(cwd)
        self.assertIsNotNone(plain)
        self.assertEqual(isolated, plain)
        return isolated

    async def test_clean_tree(self) -> None:
        """Nothing changed: zeroes all round, branch named."""
        repo = self.make_repo()

        summary = await self.assert_same_as_plain(repo)

        self.assertEqual(summary.branch, "main")
        self.assertEqual(summary.insertions, 0)

    async def test_every_file_state(self) -> None:
        """Staged, unstaged, both, untracked, renamed, deleted."""
        repo = self.make_repo()
        (repo / "c.txt").write_text("c\n", encoding="utf-8")
        self.git(repo, "add", "c.txt")
        self.git(repo, "commit", "-q", "-m", "more")

        (repo / "a.txt").write_text("1\nTWO\n3\n4\n", encoding="utf-8")
        self.git(repo, "add", "a.txt")
        (repo / "a.txt").write_text("1\nTWO\n3\n4\n5\n", encoding="utf-8")
        (repo / "b.py").write_text("x = 2\ny = 3\n", encoding="utf-8")
        self.git(repo, "mv", "c.txt", "renamed.txt")
        (repo / "new.md").write_text("n\n", encoding="utf-8")
        (repo / "untracked_dir").mkdir()
        (repo / "untracked_dir" / "f").write_text("f\n", encoding="utf-8")

        summary = await self.assert_same_as_plain(repo)

        self.assertGreater(summary.staged, 0)
        self.assertGreater(summary.unstaged, 0)
        self.assertEqual(summary.untracked, 2)

    async def test_merge_conflict(self) -> None:
        """Unmerged entries survive the copied index."""
        repo = self.make_repo()
        self.git(repo, "checkout", "-q", "-b", "other")
        (repo / "a.txt").write_text("other\n", encoding="utf-8")
        self.git(repo, "commit", "-q", "-am", "other")
        self.git(repo, "checkout", "-q", "main")
        (repo / "a.txt").write_text("mine\n", encoding="utf-8")
        self.git(repo, "commit", "-q", "-am", "mine")
        subprocess.run(
            ["git", "merge", "-q", "other"],
            cwd=repo,
            capture_output=True,
            check=False,
        )

        summary = await self.assert_same_as_plain(repo)

        self.assertEqual(summary.conflicted, 1)

    async def _make_tracked_pair(self) -> Path:
        """A clone one commit ahead of and one behind its upstream."""
        origin = self.make_repo("origin")
        clone = self.root / "clone"
        self.git(self.root, "clone", "-q", str(origin), str(clone))
        (origin / "a.txt").write_text("upstream\n", encoding="utf-8")
        self.git(origin, "commit", "-q", "-am", "upstream")
        self.git(clone, "fetch", "-q")
        (clone / "b.py").write_text("local\n", encoding="utf-8")
        self.git(clone, "commit", "-q", "-am", "local")
        return clone

    async def test_upstream_with_loose_refs(self) -> None:
        """``branch.ab`` needs branch, remote and tracking ref copied."""
        clone = await self._make_tracked_pair()

        summary = await self.assert_same_as_plain(clone)

        self.assertEqual((summary.ahead, summary.behind), (1, 1))

    async def test_upstream_with_packed_refs(self) -> None:
        """Same, after ``pack-refs`` moved every ref into one file."""
        clone = await self._make_tracked_pair()
        self.git(clone, "pack-refs", "--all")

        summary = await self.assert_same_as_plain(clone)

        self.assertEqual((summary.ahead, summary.behind), (1, 1))

    async def test_shallow_clone(self) -> None:
        """A depth-limited clone's ahead/behind walk stops at the cut.

        Git learns where from ``.git/shallow``; without it the walk asks
        for parents that were never fetched and ``status`` fails.
        """
        origin = self.make_repo("origin")
        for line in ("4", "5"):
            with open(origin / "a.txt", "a", encoding="utf-8") as file:
                file.write(f"{line}\n")
            self.git(origin, "commit", "-q", "-am", line)
        clone = self.root / "shallow"
        self.git(
            self.root,
            "clone",
            "-q",
            "--depth",
            "1",
            origin.as_uri(),
            str(clone),
        )
        (clone / "b.py").write_text("local\n", encoding="utf-8")
        self.git(clone, "commit", "-q", "-am", "local")

        summary = await self.assert_same_as_plain(clone)

        self.assertEqual((summary.ahead, summary.behind), (1, 0))

    async def test_branch_name_needing_quotes(self) -> None:
        """A branch whose name needs escaping in a subsection header.

        ``#`` and ``;`` start comments in git config, ``"`` must be
        escaped inside a quoted subsection. Windows file names cannot
        hold ``"``, so git cannot create that ref there; the comment
        characters are still exercised on every platform.
        """
        branch = "fix/#;x" if os.name == "nt" else 'fix/"quoted"#;x'
        clone = await self._make_tracked_pair()
        self.git(clone, "checkout", "-q", "-b", branch)
        self.git(clone, "branch", "-q", "-u", "origin/main")

        summary = await self.assert_same_as_plain(clone)

        self.assertEqual(summary.branch, branch)
        self.assertIsNotNone(summary.ahead)

    async def test_no_upstream(self) -> None:
        """Without one, ahead and behind stay ``None``, not zero."""
        repo = self.make_repo()

        summary = await self.assert_same_as_plain(repo)

        self.assertIsNone(summary.ahead)

    async def test_detached_head(self) -> None:
        """A detached HEAD reports the commit and no branch."""
        repo = self.make_repo()
        self.git(repo, "checkout", "-q", "--detach")

        summary = await self.assert_same_as_plain(repo)

        self.assertIsNone(summary.branch)
        self.assertIsNotNone(summary.head)

    async def test_unborn_branch(self) -> None:
        """No commits yet: the branch is named, line counts are zero."""
        repo = self.root / "empty"
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "trunk")
        (repo / "f").write_text("f\n", encoding="utf-8")

        summary = await self.assert_same_as_plain(repo)

        self.assertEqual(summary.branch, "trunk")
        self.assertIsNone(summary.head)

    async def test_sha256_repository(self) -> None:
        """The object format is copied, or no object would be found."""
        probe = subprocess.run(
            ["git", "init", "-q", "--object-format=sha256", "probe"],
            cwd=self.root,
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            self.skipTest("this git predates SHA-256 repositories")
        repo = self.make_repo("sha256", "--object-format=sha256")
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")

        summary = await self.assert_same_as_plain(repo)

        self.assertEqual(len(summary.head), 64)

    async def test_local_line_ending_and_mode_settings(self) -> None:
        """Repository-local switches that change what counts as changed.

        ``filemode = false`` is what git writes on a filesystem without
        an executable bit; dropping it would flag every file there.
        """
        repo = self.make_repo()
        self.git(repo, "config", "core.filemode", "false")
        self.git(repo, "config", "core.autocrlf", "input")
        (repo / "b.py").chmod(0o755)
        (repo / "a.txt").write_bytes(b"1\r\n2\r\n3\r\nnew\r\n")

        summary = await self.assert_same_as_plain(repo)

        self.assertEqual(summary.insertions, 1)

    async def test_binary_threshold_and_diff_algorithm(self) -> None:
        """Local settings that change the counts, not just the files.

        Over ``bigFileThreshold`` a file is binary and adds no lines;
        the diff algorithm decides how a change splits into lines.
        """
        repo = self.make_repo()
        self.git(repo, "config", "core.bigFileThreshold", "8")
        self.git(repo, "config", "diff.algorithm", "histogram")
        (repo / "a.txt").write_text("1\n2\n3\n4\n5\n", encoding="utf-8")

        summary = await self.assert_same_as_plain(repo)

        self.assertEqual(summary.insertions, 0)

    async def test_implicit_true_boolean(self) -> None:
        """A key with no ``=`` is true; an empty value would be false.

        ``status.renames`` makes the difference visible on every
        platform: on, a staged rename is one record; off, it is a
        deletion plus an addition, and ``staged`` doubles.
        """
        repo = self.make_repo()
        self.git(repo, "mv", "a.txt", "moved.txt")
        with open(repo / ".git" / "config", "a", encoding="utf-8") as file:
            file.write("[status]\n\trenames\n")

        summary = await self.assert_same_as_plain(repo)

        self.assertEqual(summary.staged, 1)

    async def test_racily_clean_entry(self) -> None:
        """A same-size edit no newer than the index is still seen.

        Git re-reads such an entry because its stat data cannot be
        trusted — the "racy git" rule, keyed on the index file's own
        mtime. Stamping the shadow's copy with the time of copying
        would switch the rule off and report the edit as clean.

        Staged exactly rather than hoped for: ``checkstat = minimal``
        and ``trustctime = false`` take the inode change time, which a
        test cannot set, out of the comparison; the entry is recorded
        with a past mtime by a refresh written *after* it, so git does
        not smudge it; then the edit and the index are both stamped
        back to that same past second.
        """
        repo = self.make_repo()
        self.git(repo, "config", "core.checkstat", "minimal")
        self.git(repo, "config", "core.trustctime", "false")
        target = repo / "a.txt"
        past = target.stat().st_mtime_ns - 100 * 10**9
        os.utime(target, ns=(past, past))
        self.git(repo, "update-index", "--really-refresh")

        target.write_text("1\nX\n3\n", encoding="utf-8")
        os.utime(target, ns=(past, past))
        os.utime(repo / ".git" / "index", ns=(past, past))

        summary = await self.assert_same_as_plain(repo)

        self.assertEqual((summary.insertions, summary.deletions), (1, 1))

    async def test_split_index(self) -> None:
        """A split index names its shared half by file; both are copied."""
        repo = self.make_repo()
        self.git(repo, "update-index", "--split-index")
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")

        await self.assert_same_as_plain(repo)

    async def test_info_exclude(self) -> None:
        """Ignore rules from ``.git/info/exclude`` still apply."""
        repo = self.make_repo()
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "exclude").write_text(
            "*.log\n",
            encoding="utf-8",
        )
        (repo / "debug.log").write_text("x\n", encoding="utf-8")
        (repo / "kept.txt").write_text("x\n", encoding="utf-8")

        summary = await self.assert_same_as_plain(repo)

        self.assertEqual(summary.untracked, 1)

    async def test_subdirectory(self) -> None:
        """The whole repository is summarised from any depth."""
        repo = self.make_repo()
        sub = repo / "pkg"
        sub.mkdir()
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")

        await self.assert_same_as_plain(sub)

    async def test_submodule_checkout(self) -> None:
        """A submodule's ``.git`` is a gitfile into the parent's."""
        inner = self.make_repo("inner")
        outer = self.make_repo("outer")
        self.git(
            outer,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(inner),
            "lib",
        )
        lib = outer / "lib"
        self.assertTrue((lib / ".git").is_file())
        (lib / "a.txt").write_text("changed\n", encoding="utf-8")

        summary = await self.assert_same_as_plain(lib)

        self.assertEqual(summary.insertions, 1)


class HostGitRefusalsTest(_GitTestBase):
    """Directories that give no badge, exactly as before or safer."""

    async def test_not_a_repository(self) -> None:
        """No ``.git`` anywhere above: nothing to report."""
        plain_dir = self.root / "plain"
        plain_dir.mkdir()

        self.assertIsNone(await self.isolated(plain_dir))

    async def test_missing_directory(self) -> None:
        """A cwd that does not exist reports nothing, not an error."""
        self.assertIsNone(await self.isolated(self.root / "absent"))

    async def test_inside_the_git_directory(self) -> None:
        """Git refuses ``status`` there; so does the isolated walk."""
        repo = self.make_repo()

        self.assertIsNone(await self.isolated(repo / ".git"))
        self.assertIsNone(await self.isolated(repo / ".git" / "refs"))

    async def test_standalone_bare_repository(self) -> None:
        """A bare repository has no tree to summarise."""
        bare = self.root / "bare.git"
        self.git(self.root, "init", "-q", "--bare", str(bare))

        self.assertIsNone(await self.isolated(bare))

    async def test_unknown_format_version(self) -> None:
        """A newer format may keep state where the copy cannot see."""
        repo = self.make_repo()
        self.git(repo, "config", "core.repositoryformatversion", "2")

        self.assertIsNone(await self.isolated(repo))

    async def test_reftable_ref_storage(self) -> None:
        """Refs in reftable are not loose files plus ``packed-refs``."""
        repo = self.make_repo()
        self.git(repo, "config", "core.repositoryformatversion", "1")
        self.git(repo, "config", "extensions.refStorage", "reftable")

        self.assertIsNone(await self.isolated(repo))

    async def test_malformed_config(self) -> None:
        """Unparseable config: real git refuses too."""
        repo = self.make_repo()
        (repo / ".git" / "config").write_text(
            "[core\n\tbroken",
            encoding="utf-8",
        )

        self.assertIsNone(await self.isolated(repo))

    async def test_git_binary_missing(self) -> None:
        """No ``git`` on PATH reads as nothing to report."""
        repo = self.make_repo()

        with patch.dict(os.environ, {"PATH": str(self.root / "nowhere")}):
            self.assertIsNone(await self.isolated(repo))


class _CountingLocalBackend(LocalBackend):
    """A real ``LocalBackend`` that counts ``git config`` parses."""

    def __init__(self) -> None:
        super().__init__()
        self.parses = 0

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Count config parses, then run the command for real."""
        if "config" in command and "--list" in command:
            self.parses += 1
        return await super().exec_shell(command, cwd=cwd, timeout=timeout)


class HostGitConfigCacheTest(_GitTestBase):
    """The parse result is reused only for byte-identical config."""

    def setUp(self) -> None:
        """Count parses; the base class already empties the cache."""
        super().setUp()
        self.backend = _CountingLocalBackend()

    async def test_unchanged_config_is_parsed_once(self) -> None:
        """Repeated polls of one repository spawn no further parse."""
        repo = self.make_repo()
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")

        for _ in range(3):
            self.assertIsNotNone(await self.isolated(repo))

        self.assertEqual(self.backend.parses, 1)

    async def test_changed_config_is_parsed_again(self) -> None:
        """New bytes are a new key: the change shows on the next poll."""
        repo = self.make_repo()
        (repo / "a.txt").write_bytes(b"1\r\n2\r\n3\r\nnew\r\n")
        await self.assert_matches_plain(repo)

        self.git(repo, "config", "core.autocrlf", "input")
        summary = await self.assert_matches_plain(repo)

        self.assertEqual(self.backend.parses, 2)
        self.assertEqual(summary.insertions, 1)

    async def test_failed_parse_is_not_cached(self) -> None:
        """A failure may be transient; the next poll must try again."""
        repo = self.make_repo()

        with patch.dict(os.environ, {"PATH": str(self.root / "nowhere")}):
            self.assertIsNone(await self.isolated(repo))
        self.assertIsNotNone(await self.isolated(repo))

        self.assertEqual(self.backend.parses, 2)

    async def test_hostile_config_never_reaches_the_cache(self) -> None:
        """Only allow-listed keys are ever stored, hit or miss."""
        repo = self.make_repo()
        self.git(
            repo,
            "config",
            "core.fsmonitor",
            f"{self.trap_command('fsmonitor')}; false",
        )
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")

        for _ in range(2):
            await self.isolated(repo)

        self.assertEqual(self.tripped(), set())
        for rendered in _git_isolation._RENDERED_CONFIG_CACHE.values():
            self.assertNotIn(b"fsmonitor", rendered)

    async def test_cache_is_bounded(self) -> None:
        """The oldest entries go once the bound is reached."""
        bound = _git_isolation._RENDERED_CONFIG_CACHE_SIZE
        repo = self.make_repo()
        with patch.object(_git_isolation, "_RENDERED_CONFIG_CACHE_SIZE", 2):
            for value in ("1", "2", "3"):
                self.git(repo, "config", "diff.renamelimit", value)
                await self.isolated(repo)

            self.assertEqual(len(_git_isolation._RENDERED_CONFIG_CACHE), 2)
        self.assertGreater(bound, 2)

    async def assert_matches_plain(self, cwd: Path) -> Any:
        """Isolated and plain summaries agree on every field."""
        plain = await self.plain(cwd)
        isolated = await self.isolated(cwd)
        self.assertIsNotNone(plain)
        self.assertEqual(isolated, plain)
        return isolated


class HostGitHygieneTest(_GitTestBase):
    """What the isolation leaves behind, and what it never touches."""

    async def test_shadow_is_removed(self) -> None:
        """Every run cleans up its temporary directory."""
        repo = self.make_repo()
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")

        for _ in range(3):
            await self.isolated(repo)

        self.assertEqual(list(self.tmp.iterdir()), [])

    async def test_shadow_is_removed_when_the_body_raises(self) -> None:
        """Cleanup does not depend on the caller succeeding."""
        repo = self.make_repo()

        with self.assertRaises(RuntimeError):
            async with isolated_git_argv(
                self.backend,
                str(repo),
                timeout=5.0,
            ) as argv:
                self.assertIsNotNone(argv)
                raise RuntimeError("caller failed")

        self.assertEqual(list(self.tmp.iterdir()), [])

    async def test_real_repository_is_not_modified(self) -> None:
        """Index, config and refs are read, never written."""
        repo = self.make_repo()
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")
        git_dir = repo / ".git"
        before = {
            path.relative_to(git_dir): path.read_bytes()
            for path in git_dir.rglob("*")
            if path.is_file()
        }

        await self.isolated(repo)

        after = {
            path.relative_to(git_dir): path.read_bytes()
            for path in git_dir.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    async def test_shadow_config_holds_only_allow_listed_keys(self) -> None:
        """No remote URL, no driver, no include reaches the shadow."""
        repo = self.make_repo()
        for key, value in (
            ("remote.origin.url", "ext::sh -c touch% /tmp/x"),
            ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
            ("branch.main.remote", "origin"),
            ("branch.main.merge", "refs/heads/main"),
            ("filter.x.clean", "evil"),
            ("diff.x.textconv", "evil"),
            ("core.pager", "evil"),
            ("core.hooksPath", "/evil"),
            ("core.sshCommand", "evil"),
            ("alias.st", "!evil"),
        ):
            self.git(repo, "config", key, value)

        async with isolated_git_argv(
            self.backend,
            str(repo),
            timeout=5.0,
        ) as argv:
            self.assertIsNotNone(argv)
            shadow = Path(argv[1].removeprefix("--git-dir="))
            listed = self.git(
                shadow,
                "config",
                "--file",
                str(shadow / "config"),
                "--list",
            )

        keys = {line.split("=", 1)[0] for line in listed.splitlines()}
        self.assertLessEqual(
            {"branch.main.remote", "branch.main.merge", "remote.origin.fetch"},
            keys,
        )
        for key in keys:
            section, _, rest = key.partition(".")
            subsection, dot, name = rest.rpartition(".")
            self.assertTrue(
                key in _git_isolation._COPIED_KEYS
                or (
                    bool(dot and subsection)
                    and (section, name)
                    in _git_isolation._COPIED_SUBSECTION_KEYS
                ),
                f"{key} is not on the allow-list",
            )
        for key in (
            "remote.origin.url",
            "filter.x.clean",
            "diff.x.textconv",
            "core.pager",
            "core.hookspath",
            "core.sshcommand",
            "alias.st",
        ):
            self.assertNotIn(key, keys)
