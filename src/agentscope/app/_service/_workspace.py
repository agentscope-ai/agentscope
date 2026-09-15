# -*- coding: utf-8 -*-
"""Workspace service — resolution, download tokens, uploads, git."""
import asyncio
import re
import tarfile
from typing import AsyncIterator, Literal

from fastapi import HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from ._download_token import (
    DEFAULT_DOWNLOAD_TOKEN_TTL,
    sign_download_token,
    verify_download_token,
)
from ..storage import SessionRecord, StorageBase
from ..workspace_manager import WorkspaceManagerBase
from ..._logging import logger
from ...tool import BackendBase
from ...workspace import WorkspaceBase

#: Ceilings on one upload. Peak memory is a chunk per concurrent
#: install; these bound the sandbox's disk and how long a slot is held.
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_FILE_COUNT = 100
MAX_CONCURRENT_INSTALLS = 5

_CHUNK_SIZE = 64 * 1024
_TAR_BLOCK = 512

# Long enough for a cold index on a large repository, short enough that
# a wedged sandbox does not hold the request open.
_GIT_TIMEOUT_SECS = 5.0

_GIT_STATUS_ARGV: tuple[str, ...] = (
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

_GIT_SHORTSTAT_ARGV: tuple[str, ...] = (
    "git",
    "--no-optional-locks",
    "diff",
    "--shortstat",
    "HEAD",
)

_INSERTIONS_RE = re.compile(rb"(\d+) insertion")
_DELETIONS_RE = re.compile(rb"(\d+) deletion")
_BRANCH_AB_RE = re.compile(r"^\+(\d+) -(\d+)$")


class SkillUploadError(ValueError):
    """Raised when an upload is malformed or over a limit."""


class UploadEntry(BaseModel):
    """One file in a folder upload, as the browser described it."""

    path: str = Field(
        description=(
            "The file's path relative to the picked folder's parent, "
            "i.e. ``webkitRelativePath``."
        ),
    )
    size: int = Field(ge=0, description="The file's size in bytes.")


class UploadManifest(BaseModel):
    """The declared contents of a folder upload."""

    entries: list[UploadEntry]


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
    staged: int = Field(default=0, description="Files with staged changes.")
    unstaged: int = Field(
        default=0,
        description="Files with unstaged changes.",
    )
    untracked: int = Field(default=0, description="Files git is not tracking.")
    conflicted: int = Field(
        default=0,
        description="Files with unresolved merge conflicts.",
    )


class WorkspaceStatus(BaseModel):
    """Where a session is pointed, and the git state of that place."""

    workdir: str = Field(
        description=(
            "Absolute path of the workspace root. Backend-dependent — a "
            "host directory locally, a fixed path inside the sandbox "
            "otherwise — so the client cannot derive it."
        ),
    )
    cwd: str = Field(
        description=(
            "Absolute path the session is focused on, resolved from "
            ":attr:`SessionConfig.cwd`. Equals :attr:`workdir` when that "
            "is unset."
        ),
    )
    git: GitStatus | None = Field(
        default=None,
        description=(
            "Git state of :attr:`cwd`, or null when there is none to "
            "report — not a repository, git unavailable, the command "
            "timed out, or the backend could not run it. The caller "
            "shows no branch either way, so the reasons are logged "
            "rather than returned."
        ),
    )


class WorkspaceService:
    """Everything behind ``/workspace/*`` that is not HTTP plumbing.

    Args:
        storage (`StorageBase`):
            Persistent storage, used to look session records up.
        workspace_manager (`WorkspaceManagerBase`):
            Opens or reattaches the workspace behind a session.
        download_secret (`str`):
            App-wide secret backing the download capability tokens.
    """

    def __init__(
        self,
        storage: StorageBase,
        workspace_manager: WorkspaceManagerBase,
        download_secret: str,
    ) -> None:
        """Bind dependencies."""
        self._storage = storage
        self._workspace_manager = workspace_manager
        self._download_secret = download_secret
        #: Serializes installs, so a burst cannot multiply into an
        #: unbounded number of open sandbox writes.
        self._install_slots = asyncio.Semaphore(MAX_CONCURRENT_INSTALLS)

    async def resolve(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> WorkspaceBase:
        """Return the workspace backing a session.

        Args:
            user_id (`str`): The authenticated user ID.
            agent_id (`str`): The agent owning the session.
            session_id (`str`): The session to resolve.

        Returns:
            `WorkspaceBase`: The session's workspace.

        Raises:
            `HTTPException`: 404 when the session does not exist.
        """
        _, workspace = await self._resolve_record(
            user_id,
            agent_id,
            session_id,
        )
        return workspace

    async def read_status(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> WorkspaceStatus:
        """Report where a session sits and the git state of that place.

        Args:
            user_id (`str`): The authenticated user ID.
            agent_id (`str`): The agent owning the session.
            session_id (`str`): The session to report on.

        Returns:
            `WorkspaceStatus`: The workspace root, the resolved
            directory, and its git summary when there is one.

        Raises:
            `HTTPException`: 404 when the session does not exist.
        """
        record, workspace = await self._resolve_record(
            user_id,
            agent_id,
            session_id,
        )
        backend = workspace.get_backend()
        # Stored as the user gave it: absolute, relative to a root the
        # client cannot see, or unset for the root itself.
        cwd = backend.abspath(record.config.cwd or "", cwd=workspace.workdir)
        return WorkspaceStatus(
            workdir=workspace.workdir,
            cwd=cwd,
            git=await self._read_git(backend, cwd),
        )

    async def _resolve_record(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> tuple[SessionRecord, WorkspaceBase]:
        """Load a session and open the workspace it is bound to.

        Args:
            user_id (`str`): The authenticated user ID.
            agent_id (`str`): The agent owning the session.
            session_id (`str`): The session to resolve.

        Returns:
            `tuple[SessionRecord, WorkspaceBase]`: The record and its
            workspace.

        Raises:
            `HTTPException`: 404 when the session does not exist.
        """
        session_record = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if session_record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id!r} not found.",
            )
        workspace = await self._workspace_manager.get_workspace(
            user_id,
            agent_id,
            session_id,
            session_record.config.workspace_id,
        )
        return session_record, workspace

    # ── Download tokens ────────────────────────────────────────────────
    #
    # A browser only streams to disk when it issues the request itself,
    # and such a request carries no custom header — so the credential
    # travels in the URL as a capability: this user, this path, briefly.

    def sign_download_token(
        self,
        user_id: str,
        path: str,
        ttl: int = DEFAULT_DOWNLOAD_TOKEN_TTL,
    ) -> tuple[str, int]:
        """Mint a token authorizing one download.

        Args:
            user_id (`str`): The already-authenticated caller.
            path (`str`): The path the token authorizes, verbatim as
                the download request will send it.
            ttl (`int`, defaults to 60): Seconds the token stays valid.

        Returns:
            `tuple[str, int]`: The token and its expiry as a Unix
            timestamp.
        """
        return sign_download_token(
            self._download_secret,
            user_id,
            path,
            ttl=ttl,
        )

    def verify_download_token(self, token: str, path: str) -> str:
        """Return the user a token was granted to, for this path.

        The path is not read out of the token but re-derived from the
        request, so a token for one file cannot be replayed against
        another.

        Args:
            token (`str`): The token from the request.
            path (`str`): The path the request is asking for.

        Returns:
            `str`: The user ID the token was minted for.

        Raises:
            `ValueError`: The token is malformed, expired, or does not
                match.
        """
        return verify_download_token(self._download_secret, token, path)

    # ── Skill upload ───────────────────────────────────────────────────
    #
    # A tar header needs each member's size before its bytes, and
    # multipart parts do not declare one — hence the manifest, trusted
    # to build the headers and verified as the bytes go by.

    @staticmethod
    def validate_manifest(manifest: UploadManifest, file_count: int) -> str:
        """Check a manifest and return the skill's root directory name.

        Args:
            manifest (`UploadManifest`): The declared upload contents.
            file_count (`int`): How many parts actually arrived.

        Returns:
            `str`: The single top-level directory every path sits under.

        Raises:
            SkillUploadError: If a limit is exceeded, a path is unsafe,
                the paths span more than one root, no ``SKILL.md`` is
                present, or the parts do not match the manifest.
        """
        entries = manifest.entries
        if not entries:
            raise SkillUploadError("The upload contains no files.")
        if file_count != len(entries):
            raise SkillUploadError(
                f"The manifest lists {len(entries)} files but "
                f"{file_count} were sent.",
            )
        if len(entries) > MAX_FILE_COUNT:
            raise SkillUploadError(
                f"A skill may hold at most {MAX_FILE_COUNT} files, "
                f"got {len(entries)}.",
            )

        total = 0
        roots: set[str] = set()
        for entry in entries:
            if entry.size > MAX_FILE_BYTES:
                raise SkillUploadError(
                    f"{entry.path!r} is {entry.size} bytes, over the "
                    f"{MAX_FILE_BYTES}-byte per-file limit.",
                )
            total += entry.size

            parts = entry.path.replace("\\", "/").split("/")
            if len(parts) < 2 or any(p in ("", ".", "..") for p in parts):
                raise SkillUploadError(f"Unsafe upload path: {entry.path!r}")
            if entry.path.startswith("/"):
                raise SkillUploadError(f"Unsafe upload path: {entry.path!r}")
            roots.add(parts[0])

        if total > MAX_TOTAL_BYTES:
            raise SkillUploadError(
                f"The upload is {total} bytes, over the "
                f"{MAX_TOTAL_BYTES}-byte limit.",
            )
        if len(roots) != 1:
            raise SkillUploadError(
                f"A skill must be a single folder, got {sorted(roots)}.",
            )

        root = roots.pop()
        if not any(e.path == f"{root}/SKILL.md" for e in manifest.entries):
            raise SkillUploadError(f"No SKILL.md at the root of {root!r}.")
        return root

    async def install_skill(
        self,
        workspace: WorkspaceBase,
        stream: AsyncIterator[bytes],
        archive_format: Literal["zip", "tar", "tar.gz"],
        name: str,
        *,
        agent_id: str | None = None,
    ) -> None:
        """Pipe a skill archive into a workspace, one install at a time.

        Takes the workspace rather than a session, because the
        from-library path resolves once and then installs several.

        Args:
            workspace (`WorkspaceBase`): The target workspace.
            stream (`AsyncIterator[bytes]`): The archive's bytes.
            archive_format (`Literal["zip", "tar", "tar.gz"]`): How the
                stream is packed.
            name (`str`): The skill's name, for the backend's logging.
            agent_id (`str | None`, optional): The agent taking
                ownership. ``None`` installs into the shared partition.
        """
        async with self._install_slots:
            await workspace.add_skill_archive(
                stream,
                archive_format,
                name,
                agent_id=agent_id,
            )

    @staticmethod
    async def tar_stream(
        manifest: UploadManifest,
        files: list[UploadFile],
    ) -> AsyncIterator[bytes]:
        """Emit a tar of the uploaded files, chunk by chunk.

        Args:
            manifest (`UploadManifest`): The validated manifest.
            files (`list[UploadFile]`): The parts, in manifest order.

        Yields:
            `bytes`: The next chunk of the tar archive.

        Raises:
            SkillUploadError: If a part's real length differs from its
                declared size.
        """
        for entry, upload in zip(manifest.entries, files):
            info = tarfile.TarInfo(name=entry.path)
            info.size = entry.size
            info.mtime = 0
            yield info.tobuf(tarfile.GNU_FORMAT)

            written = 0
            while chunk := await upload.read(_CHUNK_SIZE):
                written += len(chunk)
                if written > entry.size:
                    raise SkillUploadError(
                        f"{entry.path!r} is larger than its declared "
                        f"{entry.size} bytes.",
                    )
                yield chunk

            if written != entry.size:
                raise SkillUploadError(
                    f"{entry.path!r} declared {entry.size} bytes but "
                    f"sent {written}.",
                )
            padding = -entry.size % _TAR_BLOCK
            if padding:
                yield b"\0" * padding

        # Two zero blocks mark the end of a tar archive.
        yield b"\0" * (2 * _TAR_BLOCK)

    # ── Git status ─────────────────────────────────────────────────────

    async def _read_git(
        self,
        backend: BackendBase,
        cwd: str,
    ) -> GitStatus | None:
        """Summarise the git state of ``cwd``, or return ``None``.

        Two argv invocations rather than one shell line: ``exec_shell``
        runs argv directly, so chaining would mean wrapping in
        ``/bin/sh``, which a Windows ``LocalBackend`` does not have.
        Keeping them apart also means a failing ``diff`` still leaves
        the branch on screen.

        Never raises. A directory that is not a repository is the
        ordinary case, and the backends disagree on how they report
        trouble — Docker and K8s raise transport errors out of
        ``exec_shell`` while the others fold everything into a non-zero
        result — so every failure reads as "nothing to report".

        Args:
            backend (`BackendBase`): The workspace's backend; the only
                way into a sandbox.
            cwd (`str`): Absolute directory to inspect.

        Returns:
            `GitStatus | None`: The summary, or ``None`` when git had
            nothing to say.
        """
        try:
            status_result = await backend.exec_shell(
                list(_GIT_STATUS_ARGV),
                cwd=cwd,
                timeout=_GIT_TIMEOUT_SECS,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("git status failed in %s: %s", cwd, exc)
            return None

        if not status_result.ok():
            # 128 not a repository, 127 no git binary or bad cwd, -1
            # timeout. None distinguishable enough to explain to a user.
            logger.debug(
                "git status exited %d in %s",
                status_result.exit_code,
                cwd,
            )
            return None

        summary = self._parse_porcelain_v2(status_result.stdout)
        if summary.branch is None and summary.head is None:
            # Real git always names one or the other — a detached HEAD
            # has a commit, an unborn branch has a name.
            logger.debug("git status gave no branch in %s", cwd)
            return None

        try:
            diff_result = await backend.exec_shell(
                list(_GIT_SHORTSTAT_ARGV),
                cwd=cwd,
                timeout=_GIT_TIMEOUT_SECS,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("git diff failed in %s: %s", cwd, exc)
            return summary

        if not diff_result.ok():
            # Expected on a repository with no commits, where HEAD is
            # unborn.
            return summary

        insertions, deletions = self._parse_shortstat(diff_result.stdout)
        return summary.model_copy(
            update={"insertions": insertions, "deletions": deletions},
        )

    @staticmethod
    def _parse_porcelain_v2(stdout: bytes) -> GitStatus:
        """Parse ``git status --porcelain=v2 --branch -z`` output.

        Line counts are left at zero; they come from the separate
        ``--shortstat`` run.

        Args:
            stdout (`bytes`): Raw stdout. Unparseable records are
                skipped rather than raising: a status that omits one odd
                entry is still worth showing.

        Returns:
            `GitStatus`: Branch, upstream divergence and file counts.
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
                        ahead = int(match.group(1))
                        behind = int(match.group(2))
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
                # In -z mode a rename's original path is its own
                # NUL-separated field, so this record spans two chunks.
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

    @staticmethod
    def _parse_shortstat(stdout: bytes) -> tuple[int, int]:
        """Parse ``git diff --shortstat`` into insertions and deletions.

        Args:
            stdout (`bytes`): Raw stdout, e.g. ``" 2 files changed, 15
                insertions(+)"``. Either clause is absent when its count
                is zero, and the line is empty for a clean tree.

        Returns:
            `tuple[int, int]`: Counts, zero for whatever git omitted.
        """
        insertions = _INSERTIONS_RE.search(stdout)
        deletions = _DELETIONS_RE.search(stdout)
        return (
            int(insertions.group(1)) if insertions else 0,
            int(deletions.group(1)) if deletions else 0,
        )
