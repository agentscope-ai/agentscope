# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Git status parser test case — pure functions, no git binary needed.

``porcelain=v2`` is a stable documented format, but three of its corners
are easy to get wrong and each is asserted below: a rename record spans
two NUL-separated fields under ``-z``, ``branch.ab`` is absent rather
than zero when no upstream is configured, and an ``MM`` entry counts on
both the staged and the unstaged side.
"""
from unittest import TestCase

from agentscope.app._service import WorkspaceService

# A plausible blob/mode run; the parser only reads the XY field, but
# using realistic records keeps the fixtures honest.
_TRAILER = "N... 100644 100644 100644 1111111 2222222"


def _records(*records: str) -> bytes:
    """Join records the way ``-z`` does: NUL-separated and terminated."""
    return ("\0".join(records) + "\0").encode("utf-8")


class ParsePorcelainV2Test(TestCase):
    """Branch headers and per-file counting."""

    def test_clean_tree_with_upstream(self) -> None:
        """A clean tree in sync reports zeroes, not nulls."""
        status = WorkspaceService._parse_porcelain_v2(
            _records(
                "# branch.oid abc123def456",
                "# branch.head main",
                "# branch.upstream origin/main",
                "# branch.ab +0 -0",
            ),
        )

        self.assertEqual(status.branch, "main")
        self.assertEqual(status.head, "abc123def456")
        self.assertEqual(status.ahead, 0)
        self.assertEqual(status.behind, 0)
        self.assertEqual(status.staged, 0)
        self.assertEqual(status.unstaged, 0)

    def test_no_upstream_reports_none_not_zero(self) -> None:
        """Without an upstream git omits ``branch.ab`` entirely.

        ``None`` and ``0`` mean different things to the caller — no
        upstream to compare against versus level with one — so the
        absent line must not collapse into a count.
        """
        status = WorkspaceService._parse_porcelain_v2(
            _records("# branch.oid abc123", "# branch.head feature/x"),
        )

        self.assertEqual(status.branch, "feature/x")
        self.assertIsNone(status.ahead)
        self.assertIsNone(status.behind)

    def test_ahead_and_behind_are_read_separately(self) -> None:
        """``+2 -3`` is two counts, not a single signed number."""
        status = WorkspaceService._parse_porcelain_v2(
            _records(
                "# branch.oid abc123",
                "# branch.head main",
                "# branch.ab +2 -3",
            ),
        )

        self.assertEqual(status.ahead, 2)
        self.assertEqual(status.behind, 3)

    def test_detached_head_has_no_branch(self) -> None:
        """A detached HEAD reports its commit but no branch name."""
        status = WorkspaceService._parse_porcelain_v2(
            _records("# branch.oid deadbeef", "# branch.head (detached)"),
        )

        self.assertIsNone(status.branch)
        self.assertEqual(status.head, "deadbeef")

    def test_unborn_branch_has_no_head(self) -> None:
        """A repository with no commits names a branch but no commit."""
        status = WorkspaceService._parse_porcelain_v2(
            _records("# branch.oid (initial)", "# branch.head main"),
        )

        self.assertEqual(status.branch, "main")
        self.assertIsNone(status.head)

    def test_worktree_change_counts_as_unstaged_only(self) -> None:
        """``.M`` is modified in the worktree but not in the index."""
        status = WorkspaceService._parse_porcelain_v2(
            _records(f"1 .M {_TRAILER} a.py"),
        )

        self.assertEqual(status.staged, 0)
        self.assertEqual(status.unstaged, 1)

    def test_index_change_counts_as_staged_only(self) -> None:
        """``M.`` is staged with a clean worktree."""
        status = WorkspaceService._parse_porcelain_v2(
            _records(f"1 M. {_TRAILER} a.py"),
        )

        self.assertEqual(status.staged, 1)
        self.assertEqual(status.unstaged, 0)

    def test_partially_staged_file_counts_on_both_sides(self) -> None:
        """``MM`` is one file that is both staged and modified since.

        The two counts describe the same file from different angles, so
        a caller must never add them together.
        """
        status = WorkspaceService._parse_porcelain_v2(
            _records(f"1 MM {_TRAILER} a.py"),
        )

        self.assertEqual(status.staged, 1)
        self.assertEqual(status.unstaged, 1)

    def test_rename_consumes_its_original_path_field(self) -> None:
        """Under ``-z`` a rename's old path is its own NUL field.

        The old path here is named ``? old.py`` — legal on POSIX, and
        exactly what ``-z`` exists to survive. Left unconsumed it reads
        as an untracked record and inflates the count, which is why the
        assertion below is on ``untracked`` rather than on the rename.
        """
        status = WorkspaceService._parse_porcelain_v2(
            _records(
                f"2 R. {_TRAILER} R100 new.py",
                "? old.py",
                f"1 .M {_TRAILER} b.py",
            ),
        )

        self.assertEqual(status.staged, 1)
        self.assertEqual(status.unstaged, 1)
        self.assertEqual(status.untracked, 0)

    def test_unmerged_counts_as_conflicted_only(self) -> None:
        """``u UU``'s XY is a conflict pair, not a staged/unstaged one."""
        status = WorkspaceService._parse_porcelain_v2(
            _records("u UU N... 100644 100644 100644 100644 a b c x.py"),
        )

        self.assertEqual(status.conflicted, 1)
        self.assertEqual(status.staged, 0)
        self.assertEqual(status.unstaged, 0)

    def test_untracked_entries_are_counted(self) -> None:
        """Untracked files and collapsed directories both count once."""
        status = WorkspaceService._parse_porcelain_v2(
            _records("? notes.md", "? scratch/"),
        )

        self.assertEqual(status.untracked, 2)
        self.assertEqual(status.unstaged, 0)

    def test_paths_with_spaces_and_newlines_survive(self) -> None:
        """``-z`` is what makes an awkward filename safe to parse."""
        status = WorkspaceService._parse_porcelain_v2(
            _records(
                f"1 .M {_TRAILER} my docs/a b.py",
                "? line\nbreak.txt",
            ),
        )

        self.assertEqual(status.unstaged, 1)
        self.assertEqual(status.untracked, 1)

    def test_unknown_record_types_are_skipped(self) -> None:
        """An unrecognised prefix is ignored, not fatal.

        Reporting a status that omits one odd entry beats reporting none
        at all if git grows a record type.
        """
        status = WorkspaceService._parse_porcelain_v2(
            _records(
                "! ignored.txt",
                "x whatever",
                f"1 .M {_TRAILER} a.py",
            ),
        )

        self.assertEqual(status.unstaged, 1)

    def test_truncated_output_does_not_raise(self) -> None:
        """A cut-off final record is dropped rather than crashing."""
        truncated = _records(f"1 .M {_TRAILER} a.py") + b"1 M"

        status = WorkspaceService._parse_porcelain_v2(truncated)

        self.assertEqual(status.unstaged, 1)

    def test_empty_output(self) -> None:
        """No output at all yields a blank, valid status."""
        status = WorkspaceService._parse_porcelain_v2(b"")

        self.assertIsNone(status.branch)
        self.assertEqual(status.staged, 0)


class ParseShortstatTest(TestCase):
    """``git diff --shortstat`` line counts."""

    def test_insertions_and_deletions(self) -> None:
        """The usual case with both clauses present."""
        line = b" 20 files changed, 621 insertions(+), 182 deletions(-)\n"

        self.assertEqual(WorkspaceService._parse_shortstat(line), (621, 182))

    def test_insertions_only(self) -> None:
        """Git omits the deletions clause when nothing was removed."""
        line = b" 1 file changed, 3 insertions(+)\n"

        self.assertEqual(WorkspaceService._parse_shortstat(line), (3, 0))

    def test_deletions_only(self) -> None:
        """And omits insertions likewise."""
        line = b" 1 file changed, 4 deletions(-)\n"

        self.assertEqual(WorkspaceService._parse_shortstat(line), (0, 4))

    def test_singular_wording(self) -> None:
        """One line reads ``insertion(+)``, without the plural s."""
        line = b" 1 file changed, 1 insertion(+), 1 deletion(-)\n"

        self.assertEqual(WorkspaceService._parse_shortstat(line), (1, 1))

    def test_clean_tree_is_empty_output(self) -> None:
        """Nothing changed means no line at all, not a line of zeroes."""
        self.assertEqual(WorkspaceService._parse_shortstat(b""), (0, 0))

    def test_unrecognised_output(self) -> None:
        """Anything unparseable reads as no change rather than raising."""
        self.assertEqual(
            WorkspaceService._parse_shortstat(b"fatal: bad revision 'HEAD'"),
            (0, 0),
        )
