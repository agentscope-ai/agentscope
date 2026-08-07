# -*- coding: utf-8 -*-
"""Git status for a workspace directory — argv and output parsers.

Pure functions over the bytes ``git`` writes, with no backend or bus
import, so the parsers can be tested without a git binary.
"""
import re

from pydantic import BaseModel, Field

GIT_STATUS_ARGV: tuple[str, ...] = (
    "git",
    # ``git status`` otherwise refreshes and rewrites the index, taking
    # ``.git/index.lock`` — which the agent's own Bash may be holding.
    "--no-optional-locks",
    "status",
    "--porcelain=v2",
    "--branch",
    # NUL-separated, so a path containing a newline cannot split a record.
    "-z",
    # Collapse untracked directories; an untracked ``node_modules``
    # otherwise emits tens of thousands of records.
    "--untracked-files=normal",
    "--ignore-submodules=all",
)

GIT_SHORTSTAT_ARGV: tuple[str, ...] = (
    "git",
    "--no-optional-locks",
    "diff",
    "--shortstat",
    "HEAD",
)

_INSERTIONS_RE = re.compile(rb"(\d+) insertion")
_DELETIONS_RE = re.compile(rb"(\d+) deletion")
_BRANCH_AB_RE = re.compile(r"^\+(\d+) -(\d+)$")


class GitStatus(BaseModel):
    """Git state of one directory, as shown next to the composer."""

    branch: str | None = Field(
        default=None,
        description="Current branch, or null on a detached HEAD.",
    )
    head: str | None = Field(
        default=None,
        description=(
            "Full commit SHA of HEAD, or null when the repository has "
            "no commits yet. Never abbreviated — how many characters to "
            "show is the caller's decision."
        ),
    )
    ahead: int | None = Field(
        default=None,
        description=(
            "Commits ahead of the upstream branch. Null when no "
            "upstream is configured, which is a different state from "
            "zero: git omits the counts entirely in that case."
        ),
    )
    behind: int | None = Field(
        default=None,
        description="Commits behind the upstream branch. Null as above.",
    )
    insertions: int = Field(
        default=0,
        description=(
            "Lines added relative to HEAD. Untracked files contribute "
            "nothing — ``git diff`` does not see them — so a session "
            "that only created files reports zero here and a non-zero "
            "``untracked``."
        ),
    )
    deletions: int = Field(
        default=0,
        description="Lines removed relative to HEAD, same caveat.",
    )
    staged: int = Field(
        default=0,
        description="Files with staged changes.",
    )
    unstaged: int = Field(
        default=0,
        description="Files with unstaged changes.",
    )
    untracked: int = Field(
        default=0,
        description="Files git is not tracking.",
    )
    conflicted: int = Field(
        default=0,
        description="Files with unresolved merge conflicts.",
    )


def parse_porcelain_v2(stdout: bytes) -> GitStatus:
    """Parse ``git status --porcelain=v2 --branch -z`` output.

    Line counts are left at zero — they come from
    :data:`GIT_SHORTSTAT_ARGV`, which the caller runs separately.

    Args:
        stdout (`bytes`):
            Raw stdout. Unparseable records are skipped rather than
            raising: a status that lists one odd entry is still worth
            showing.

    Returns:
        `GitStatus`: Branch, upstream divergence and per-file counts.
    """
    branch: str | None = None
    head: str | None = None
    ahead: int | None = None
    behind: int | None = None
    staged = unstaged = untracked = conflicted = 0

    chunks = stdout.split(b"\0")
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        index += 1
        if not chunk:
            continue

        record = chunk.decode("utf-8", errors="replace")
        kind, _, rest = record.partition(" ")

        if kind == "#":
            key, _, value = rest.partition(" ")
            if key == "branch.oid":
                head = None if value == "(initial)" else value
            elif key == "branch.head":
                branch = None if value == "(detached)" else value
            elif key == "branch.ab":
                match = _BRANCH_AB_RE.match(value)
                if match:
                    ahead, behind = int(match.group(1)), int(match.group(2))
            continue

        if kind == "?":
            untracked += 1
            continue

        if kind == "u":
            # An unmerged entry's XY is the conflict pair, not a
            # staged/unstaged pair, so it counts only as conflicted.
            conflicted += 1
            continue

        if kind not in ("1", "2"):
            continue

        if kind == "2":
            # In -z mode a rename's original path is its own NUL-separated
            # field, so this record spans two chunks.
            index += 1

        xy = rest.split(" ", 1)[0]
        if len(xy) != 2:
            continue
        # ``MM`` counts on both sides — the two are not additive.
        if xy[0] != ".":
            staged += 1
        if xy[1] != ".":
            unstaged += 1

    return GitStatus(
        branch=branch,
        head=head,
        ahead=ahead,
        behind=behind,
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        conflicted=conflicted,
    )


def parse_shortstat(stdout: bytes) -> tuple[int, int]:
    """Parse ``git diff --shortstat`` into ``(insertions, deletions)``.

    Args:
        stdout (`bytes`):
            Raw stdout, e.g. ``" 2 files changed, 15 insertions(+)"``.
            Either clause is absent when its count is zero, and the
            whole line is empty for a clean tree.

    Returns:
        `tuple[int, int]`: Counts, zero for whatever git omitted.
    """
    insertions = _INSERTIONS_RE.search(stdout)
    deletions = _DELETIONS_RE.search(stdout)
    return (
        int(insertions.group(1)) if insertions else 0,
        int(deletions.group(1)) if deletions else 0,
    )
