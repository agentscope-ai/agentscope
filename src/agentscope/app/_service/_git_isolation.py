# -*- coding: utf-8 -*-
"""Run git on a host repository without loading that repository's config.

Every git command that touches the working tree loads ``.git/config``,
and several keys in it name programs git will run: ``core.fsmonitor``
on each index refresh, ``filter.<driver>.clean`` for any path the
repository's ``.gitattributes`` routes through that driver, and more.
Whoever can write that file — an uploaded skill, a cloned project, the
agent itself — therefore decides what a "read-only" ``git status`` runs
on the host. ``-c`` overrides cannot close this: git has no switch that
skips repository config, and driver names are the attacker's to choose,
so there is no finite list of keys to override.

Git is pointed at a *shadow* git directory instead: a fresh private
temporary directory holding

* a ``config`` written here from an allow-list of keys copied out of
  the real one, none of which names a program or a path;
* ``HEAD``, the index and the local-branch and remote-tracking refs,
  copied as plain bytes, each only after the opened file is confirmed
  to be a regular one;
* ``objects/info/alternates`` naming the real object store, so no
  object is copied.

Git then runs with ``--git-dir`` at the shadow and ``--work-tree`` at
the real checkout. The real config is copied like everything else and
only that copy is ever *parsed as data*, by ``git config --file`` under
the still config-less shadow; no process loads it as configuration, so
neither its contents nor a swap made after the copy can reach a command
line.

The parse is the one step that costs a process of its own, and config
almost never changes between two polls of the status badge, so its
result is cached by the SHA-256 of the bytes parsed. A key derived from
content cannot go stale: different bytes are a different key.
"""
import asyncio
import hashlib
import os
import shutil
import stat
import tempfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import AsyncIterator, NamedTuple

from ..._logging import logger
from ...tool import BackendBase

#: Repository-wide keys copied verbatim. Each one changes how git reads
#: the working tree or counts lines — stat comparison, line endings,
#: case folding, long Windows paths, the hash algorithm, the binary
#: threshold, the diff algorithm, rename detection — so copying keeps
#: the counts equal to an unshadowed run. Every value is a boolean, a
#: number or a fixed keyword; none names a program, a path or a remote.
_COPIED_KEYS = frozenset(
    {
        "core.repositoryformatversion",
        "core.filemode",
        "core.ignorecase",
        "core.precomposeunicode",
        "core.symlinks",
        "core.longpaths",
        "core.autocrlf",
        "core.eol",
        "core.checkstat",
        "core.trustctime",
        "core.bigfilethreshold",
        "extensions.objectformat",
        "diff.algorithm",
        "diff.renames",
        "diff.renamelimit",
        "status.renames",
        "status.renamelimit",
    },
)

#: ``(section, key)`` pairs copied for every subsection. Together they
#: are what git needs to name a branch's upstream for ``branch.ab``. No
#: ``remote.<name>.url`` is copied: tracking is resolved from local refs
#: alone, and an omitted URL is one fewer value that could name a
#: transport.
_COPIED_SUBSECTION_KEYS = frozenset(
    {
        ("branch", "remote"),
        ("branch", "merge"),
        ("remote", "fetch"),
    },
)

#: The only ref namespaces ``status --branch`` reads. Tags and notes are
#: left behind, which keeps the copy small on repositories with many.
_COPIED_REF_DIRS: tuple[str, ...] = ("heads", "remotes")

#: Repository formats this module can mirror. A newer format, or a ref
#: backend other than loose files plus ``packed-refs``, keeps its state
#: somewhere a byte-for-byte copy of the paths above would miss.
_SUPPORTED_FORMAT_VERSIONS = frozenset({"0", "1"})
_SUPPORTED_REF_STORAGE = frozenset({"files"})

#: Upper bound on the small pointer files read here — ``HEAD``, a
#: ``.git`` gitfile, ``commondir``. Real ones are a single short line.
_POINTER_FILE_MAX_BYTES = 4096

_COPY_CHUNK_BYTES = 1024 * 1024

#: Upper bound on the config read for parsing. A real repository config
#: is a few hundred bytes; anything this large is not one, and refusing
#: it keeps the bytes held in memory for hashing bounded.
_CONFIG_MAX_BYTES = 1024 * 1024

#: Rendered shadow configs, keyed by the SHA-256 of the real config's
#: bytes. Only successful parses are stored — a timeout or a missing
#: binary says nothing about the bytes — and the oldest entry is evicted
#: past :data:`_RENDERED_CONFIG_CACHE_SIZE`. Touched only on the event
#: loop, never from a worker thread, so it needs no lock.
_RENDERED_CONFIG_CACHE: "OrderedDict[bytes, bytes]" = OrderedDict()
_RENDERED_CONFIG_CACHE_SIZE = 64


class _Repository(NamedTuple):
    """Where the pieces of one checkout live on the host.

    Attributes:
        work_tree: Top of the checkout — the directory holding ``.git``.
        git_dir: Per-checkout state: ``HEAD`` and the index.
        common_dir: Shared state: config, refs, objects. Equals
            ``git_dir`` except in a linked worktree.
    """

    work_tree: str
    git_dir: str
    common_dir: str


def _new_scratch_dir() -> str:
    """Create the private directory the shadow repository lives in.

    Kept as a named function so ``asyncio.to_thread`` receives a plain
    callable instead of the overloaded ``tempfile.mkdtemp``.

    Returns:
        `str`: Path to a fresh directory.
    """
    return tempfile.mkdtemp(prefix="agentscope-git-")


@asynccontextmanager
async def isolated_git_argv(
    backend: BackendBase,
    cwd: str,
    *,
    timeout: float,
) -> AsyncIterator[tuple[str, ...] | None]:
    """Yield a git argv prefix that never loads the repository's config.

    The prefix replaces the bare ``"git"`` of an argv, ahead of its
    global options and subcommand. It is valid only inside the ``with``
    block: the scratch directory holding the shadow is removed on exit,
    whether or not the block raised.

    Only meaningful for a backend whose commands run on this host —
    the shadow is built with the host filesystem calls the
    :class:`BackendBase` contract otherwise warns against.

    Args:
        backend (`BackendBase`):
            A host-local backend. Used to run ``git config`` for the
            parse step, so a missing binary or a timeout surfaces as an
            ordinary :class:`ExecResult`.
        cwd (`str`):
            Absolute host directory to inspect.
        timeout (`float`):
            Seconds allowed for the config parse.

    Yields:
        `tuple[str, ...] | None`:
            ``("git", "--git-dir=…", "--work-tree=…", …)``, or ``None``
            when ``cwd`` is not inside a checkout this module can
            mirror: no ``.git`` above it, ``cwd`` inside the git
            directory itself, an unsupported repository format, or an
            unreadable piece of state.
    """
    repository = await asyncio.to_thread(_find_repository, cwd)
    if repository is None:
        yield None
        return

    scratch = await asyncio.to_thread(_new_scratch_dir)
    try:
        argv = await _populate_shadow(
            backend,
            repository,
            scratch,
            timeout=timeout,
        )
        yield argv
    finally:
        await asyncio.to_thread(shutil.rmtree, scratch, ignore_errors=True)


async def _populate_shadow(
    backend: BackendBase,
    repository: _Repository,
    scratch: str,
    *,
    timeout: float,
) -> tuple[str, ...] | None:
    """Build the shadow inside ``scratch`` and return the argv prefix.

    ``scratch`` holds the shadow git directory git is pointed at and,
    when the parse is not already cached, a copy of the real config
    that only the parse step reads. Keeping that copy outside the
    shadow means git, run against the shadow, has no path that leads to
    it.

    Args:
        backend (`BackendBase`):
            Runs the ``git config`` parse.
        repository (`_Repository`):
            The checkout being mirrored.
        scratch (`str`):
            An empty private directory to fill.
        timeout (`float`):
            Seconds allowed for the config parse.

    Returns:
        `tuple[str, ...] | None`:
            The argv prefix, or ``None`` when the repository cannot be
            mirrored faithfully.
    """
    shadow = os.path.join(scratch, "git")
    try:
        copied = await asyncio.to_thread(_copy_state, repository, shadow)
        config_bytes = (
            await asyncio.to_thread(
                _read_config_bytes,
                os.path.join(repository.common_dir, "config"),
            )
            if copied
            else None
        )
    except OSError as exc:
        logger.debug(
            "git state of %s could not be copied: %s",
            repository.work_tree,
            exc,
        )
        return None
    if config_bytes is None:
        logger.debug(
            "git state of %s is not plain files and cannot be mirrored",
            repository.work_tree,
        )
        return None

    config = await _shadow_config(
        backend,
        config_bytes,
        scratch,
        shadow,
        timeout=timeout,
    )
    if config is None:
        return None

    try:
        await asyncio.to_thread(
            _write_bytes,
            os.path.join(shadow, "config"),
            config,
        )
    except OSError as exc:
        logger.debug("shadow config could not be written: %s", exc)
        return None

    return (
        "git",
        f"--git-dir={shadow}",
        f"--work-tree={repository.work_tree}",
        # The user's own global config still loads, and may enable a
        # monitor. Its token would be checked against an index that was
        # copied, not refreshed, so a monitor here can only mislead.
        "-c",
        "core.fsmonitor=false",
    )


# ── Locating the repository ─────────────────────────────────────────────


def _find_repository(cwd: str) -> _Repository | None:
    """Walk up from ``cwd`` to the checkout git itself would pick.

    Mirrors git's discovery for the layouts a working tree can have: a
    ``.git`` directory, or a ``.git`` gitfile naming one elsewhere (a
    linked worktree or a submodule). A directory that is itself a bare
    repository is not treated as one — git's own
    ``safe.bareRepository=explicit`` stance — so an embedded bare
    repository cannot stand in for the checkout around it.

    Args:
        cwd (`str`):
            Absolute host directory to start from.

    Returns:
        `_Repository | None`:
            The checkout, or ``None`` when there is none above ``cwd``
            or ``cwd`` lies inside the git directory itself — where git
            refuses ``status`` too.
    """
    current = os.path.abspath(cwd)
    if not os.path.isdir(current):
        return None

    while True:
        dot_git = os.path.join(current, ".git")
        git_dir: str | None = None
        if os.path.isdir(dot_git):
            git_dir = dot_git
        elif os.path.isfile(dot_git):
            git_dir = _read_gitfile(dot_git)
            if git_dir is None:
                return None

        if git_dir is not None:
            return _describe_repository(current, git_dir, cwd)

        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _describe_repository(
    work_tree: str,
    git_dir: str,
    cwd: str,
) -> _Repository | None:
    """Resolve the common directory and reject ``cwd`` inside git's own.

    Args:
        work_tree (`str`):
            The directory holding ``.git``.
        git_dir (`str`):
            Where that ``.git`` points.
        cwd (`str`):
            The directory the walk started from.

    Returns:
        `_Repository | None`:
            The resolved layout, or ``None`` as described in
            :func:`_find_repository`.
    """
    git_dir = os.path.normpath(git_dir)
    common_dir = git_dir
    commondir_file = os.path.join(git_dir, "commondir")
    if os.path.lexists(commondir_file):
        pointer = _read_pointer(commondir_file)
        if pointer is None:
            return None
        common_dir = os.path.normpath(os.path.join(git_dir, pointer))

    start = os.path.normcase(os.path.abspath(cwd))
    for state_dir in (git_dir, common_dir):
        inner = os.path.normcase(state_dir)
        if start == inner or start.startswith(inner + os.sep):
            return None

    return _Repository(work_tree, git_dir, common_dir)


def _read_gitfile(path: str) -> str | None:
    """Return the directory a ``gitdir: <path>`` file points to.

    Args:
        path (`str`):
            A ``.git`` that is a file rather than a directory.

    Returns:
        `str | None`:
            The absolute git directory, or ``None`` when the file is
            malformed or names nothing.
    """
    content = _read_pointer(path)
    if content is None or not content.startswith("gitdir:"):
        return None
    target = content[len("gitdir:") :].strip()
    if not target:
        return None
    resolved = os.path.join(os.path.dirname(path), target)
    return resolved if os.path.isdir(resolved) else None


def _read_pointer(path: str) -> str | None:
    """Read a one-line pointer file, refusing anything else.

    Args:
        path (`str`):
            ``HEAD``, a gitfile or ``commondir``.

    Returns:
        `str | None`:
            The first line, stripped, or ``None`` when the file is not
            regular, is oversized, is not UTF-8, or cannot be read.
    """
    fd = _open_regular(path)
    if fd is None:
        return None
    try:
        with os.fdopen(fd, "rb") as file:
            data = file.read(_POINTER_FILE_MAX_BYTES + 1)
    except OSError:
        return None
    if len(data) > _POINTER_FILE_MAX_BYTES:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.splitlines()
    return lines[0].strip() if lines else None


def _open_regular(path: str) -> int | None:
    """Open ``path`` for reading only if it is a regular file.

    The check runs on the opened descriptor, not the path, so a file
    swapped for a FIFO or a device between a check and an open cannot
    slip through. ``O_NONBLOCK`` keeps the open itself from waiting on
    a FIFO's writer; it has no effect on a regular file.

    Args:
        path (`str`):
            The file to open. Symlinks are followed.

    Returns:
        `int | None`:
            An open descriptor the caller must close, or ``None`` when
            the path is missing, unreadable or not a regular file.
    """
    flags = (
        os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        is_regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except OSError:
        is_regular = False
    if not is_regular:
        os.close(fd)
        return None
    return fd


# ── Copying state ───────────────────────────────────────────────────────


def _copy_state(repository: _Repository, shadow: str) -> bool:
    """Copy the data ``status`` and ``diff`` read into the shadow.

    Every copy is bytes-to-bytes; nothing here is interpreted beyond
    checking that a file is regular.

    Args:
        repository (`_Repository`):
            The checkout being mirrored.
        shadow (`str`):
            The shadow git directory.

    Returns:
        `bool`:
            ``False`` when ``HEAD`` is not a readable pointer file, or
            when a piece of state exists but is not a regular file.

    Raises:
        `OSError`:
            When a file that exists cannot be copied.
    """
    head = os.path.join(repository.git_dir, "HEAD")
    if _read_pointer(head) is None:
        return False

    # Each path below is optional — an unstaged repository has no
    # index, a fresh one no packed-refs — but one that exists and
    # cannot be copied as a regular file means the shadow would
    # describe a different repository, so the mirror is abandoned.
    optional = [
        (head, "HEAD"),
        (os.path.join(repository.git_dir, "index"), "index"),
        (os.path.join(repository.common_dir, "packed-refs"), "packed-refs"),
        # Where history was cut off in a shallow clone (and the legacy
        # grafts file that does the same by hand). Without it the
        # ahead/behind walk runs into parents that were never fetched,
        # and ``status`` fails outright.
        (os.path.join(repository.common_dir, "shallow"), "shallow"),
        (
            os.path.join(repository.common_dir, "info", "grafts"),
            os.path.join("info", "grafts"),
        ),
        # Ignore rules and attributes git reads from beside the config.
        # Attributes can only name drivers, and the shadow config
        # defines none, so copying them keeps fidelity without
        # restoring any way to run one.
        (
            os.path.join(repository.common_dir, "info", "exclude"),
            os.path.join("info", "exclude"),
        ),
        (
            os.path.join(repository.common_dir, "info", "attributes"),
            os.path.join("info", "attributes"),
        ),
    ]
    # A split index names its shared half by file, beside the index.
    optional.extend(
        (os.path.join(repository.git_dir, name), name)
        for name in os.listdir(repository.git_dir)
        if name.startswith("sharedindex.")
    )
    for source, relative in optional:
        if not _copy_if_present(source, os.path.join(shadow, relative)):
            return False

    for namespace in _COPIED_REF_DIRS:
        if not _copy_ref_tree(
            os.path.join(repository.common_dir, "refs", namespace),
            os.path.join(shadow, "refs", namespace),
        ):
            return False
    os.makedirs(os.path.join(shadow, "refs"), exist_ok=True)

    objects_info = os.path.join(shadow, "objects", "info")
    os.makedirs(objects_info, exist_ok=True)
    alternate = _alternates_entry(
        os.path.join(repository.common_dir, "objects"),
    )
    if alternate is None:
        return False
    _write_bytes(os.path.join(objects_info, "alternates"), alternate)
    return True


def _copy_ref_tree(source: str, target: str) -> bool:
    """Copy every file under a ``refs/<namespace>`` directory.

    Args:
        source (`str`):
            e.g. ``<common>/refs/heads``. Absent is fine.
        target (`str`):
            The matching shadow directory.

    Returns:
        `bool`:
            ``False`` when an entry is not a regular file.
    """
    if not os.path.isdir(source):
        return True
    for root, _, files in os.walk(source):
        relative = os.path.relpath(root, source)
        for name in files:
            if not _copy_if_regular(
                os.path.join(root, name),
                os.path.normpath(os.path.join(target, relative, name)),
            ):
                return False
    return True


def _copy_if_present(source: str, target: str) -> bool:
    """Copy ``source`` if anything is there; absent is not a failure.

    Args:
        source (`str`):
            An optional piece of git state.
        target (`str`):
            Destination inside the shadow.

    Returns:
        `bool`:
            ``True`` when nothing is there or it was copied; ``False``
            when something is there that is not a regular file.
    """
    if not os.path.lexists(source):
        return True
    return _copy_if_regular(source, target)


def _copy_if_regular(source: str, target: str) -> bool:
    """Copy ``source`` to ``target`` when it is a regular file.

    Copies at most the size the opened file had when it was checked,
    so a file that keeps growing cannot fill the shadow's volume.

    The source's timestamps are carried over. For the index this is a
    correctness matter, not cosmetics: git treats an entry whose file
    is no older than the index itself as "racily clean" and re-reads
    its content, and a copy stamped *now* would make a same-size edit
    in the same second as the last index write look unchanged.

    Args:
        source (`str`):
            The file to copy. Missing or irregular is skipped.
        target (`str`):
            Destination; parent directories are created.

    Returns:
        `bool`:
            ``True`` when the file was copied.

    Raises:
        `OSError`:
            When the source opened but the copy failed part-way.
    """
    fd = _open_regular(source)
    if fd is None:
        return False
    with os.fdopen(fd, "rb") as reader:
        source_stat = os.fstat(reader.fileno())
        remaining = source_stat.st_size
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as writer:
            while remaining > 0:
                chunk = reader.read(min(_COPY_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                writer.write(chunk)
                remaining -= len(chunk)
    os.utime(
        target,
        ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
    )
    return True


def _alternates_entry(objects_dir: str) -> bytes | None:
    """Render one ``objects/info/alternates`` line for ``objects_dir``.

    Args:
        objects_dir (`str`):
            The real object store.

    Returns:
        `bytes | None`:
            The line, or ``None`` when the path cannot be written in the
            plain form — a newline would split it, and a leading ``#``
            or ``"`` would make git read a comment or a quoted path.
    """
    path = os.path.abspath(objects_dir)
    if os.sep == "\\":
        # Git for Windows reads either separator; forward slashes keep
        # the entry free of anything its unquoting could touch.
        path = path.replace("\\", "/")
    if "\n" in path or "\r" in path or path[:1] in ("#", '"'):
        return None
    return path.encode("utf-8") + b"\n"


def _write_bytes(path: str, data: bytes) -> None:
    """Write ``data`` to ``path``, replacing anything there.

    Args:
        path (`str`):
            Destination inside the shadow.
        data (`bytes`):
            The content.
    """
    with open(path, "wb") as file:
        file.write(data)


# ── Config ──────────────────────────────────────────────────────────────


def _read_config_bytes(path: str) -> bytes | None:
    """Read the real config for parsing, through the same regular-file
    gate as every copy.

    Args:
        path (`str`):
            ``<common>/config``.

    Returns:
        `bytes | None`:
            Its bytes — empty when there is no config — or ``None`` when
            something is there that is not a regular file of plausible
            size.

    Raises:
        `OSError`:
            When the file opened but could not be read.
    """
    if not os.path.lexists(path):
        return b""
    fd = _open_regular(path)
    if fd is None:
        return None
    with os.fdopen(fd, "rb") as file:
        data = file.read(_CONFIG_MAX_BYTES + 1)
    return data if len(data) <= _CONFIG_MAX_BYTES else None


async def _shadow_config(
    backend: BackendBase,
    config_bytes: bytes,
    scratch: str,
    shadow: str,
    *,
    timeout: float,
) -> bytes | None:
    """Return the shadow config for ``config_bytes``, parsing on a miss.

    Args:
        backend (`BackendBase`):
            Runs the parse on a cache miss.
        config_bytes (`bytes`):
            The real config, as read by :func:`_read_config_bytes`.
        scratch (`str`):
            Where the copy to parse is written on a miss.
        shadow (`str`):
            The shadow git directory, config-less at this point.
        timeout (`float`):
            Seconds allowed for the parse.

    Returns:
        `bytes | None`:
            The rendered config, or ``None`` when the bytes cannot be
            parsed, declare an unsupported format, or hold a value git
            config syntax cannot carry.
    """
    if not config_bytes:
        return b""

    key = hashlib.sha256(config_bytes).digest()
    cached = _RENDERED_CONFIG_CACHE.get(key)
    if cached is not None:
        _RENDERED_CONFIG_CACHE.move_to_end(key)
        return cached

    source_config = os.path.join(scratch, "source-config")
    try:
        await asyncio.to_thread(_write_bytes, source_config, config_bytes)
    except OSError as exc:
        logger.debug("config copy could not be written: %s", exc)
        return None

    entries = await _read_config_entries(
        backend,
        source_config,
        shadow,
        timeout=timeout,
    )
    if entries is None:
        return None
    rendered = _render_config(entries)
    if rendered is None:
        return None

    _RENDERED_CONFIG_CACHE[key] = rendered
    while len(_RENDERED_CONFIG_CACHE) > _RENDERED_CONFIG_CACHE_SIZE:
        _RENDERED_CONFIG_CACHE.popitem(last=False)
    return rendered


async def _read_config_entries(
    backend: BackendBase,
    config_path: str,
    shadow: str,
    *,
    timeout: float,
) -> list[tuple[str, str | None]] | None:
    """Parse a copy of the real config as data; keep allow-listed keys.

    Uses git's own parser, so quoting, continuation lines and
    case-folding are read exactly as git would. ``--file`` reads that
    one file; ``--no-includes`` stops ``include.path`` from pulling in
    others; ``--git-dir`` at the still config-less shadow means the
    parsing process discovers no repository of its own.

    Args:
        backend (`BackendBase`):
            Runs the parse.
        config_path (`str`):
            The copy written by :func:`_shadow_config` — a regular file
            of bounded size by construction.
        shadow (`str`):
            The shadow git directory.
        timeout (`float`):
            Seconds allowed.

    Returns:
        `list[tuple[str, str | None]] | None`:
            ``(name, value)`` pairs in file order — ``None`` as a value
            is an implicit ``true`` — or ``None`` when the config cannot
            be parsed or declares a repository format this module
            cannot mirror.
    """
    result = await backend.exec_shell(
        [
            "git",
            f"--git-dir={shadow}",
            "config",
            "--file",
            config_path,
            "--no-includes",
            "--null",
            "--list",
        ],
        cwd=os.path.dirname(shadow),
        timeout=timeout,
    )
    if not result.ok():
        # Malformed config — which real git would refuse as well.
        logger.debug(
            "git config --list exited %d for %s",
            result.exit_code,
            config_path,
        )
        return None

    entries: list[tuple[str, str | None]] = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        raw_name, has_value, raw_value = record.partition(b"\n")
        name = raw_name.decode("utf-8", errors="surrogateescape")
        # ``[core] filemode`` with no ``=`` is an implicit true, which
        # ``--null`` prints as a bare name — not the same as ``= ""``,
        # which reads as false.
        value = (
            raw_value.decode("utf-8", errors="surrogateescape")
            if has_value
            else None
        )

        if not _is_supported_format(name, value):
            logger.debug(
                "%s declares %s=%s, which cannot be mirrored",
                config_path,
                name,
                value,
            )
            return None
        if _is_copied(name):
            entries.append((name, value))
    return entries


def _is_supported_format(name: str, value: str | None) -> bool:
    """Return whether one config entry leaves the repository mirrorable.

    Args:
        name (`str`):
            Fully qualified, as ``git config --list`` prints it.
        value (`str | None`):
            Its value; ``None`` for an implicit ``true``.

    Returns:
        `bool`:
            ``False`` for a format version or ref backend whose state
            lives outside the files :func:`_copy_state` copies.
    """
    if name == "core.repositoryformatversion":
        return (value or "").strip() in _SUPPORTED_FORMAT_VERSIONS
    if name == "extensions.refstorage":
        return (value or "").strip().lower() in _SUPPORTED_REF_STORAGE
    return True


def _is_copied(name: str) -> bool:
    """Return whether a config entry is on the allow-list.

    Args:
        name (`str`):
            Fully qualified. ``git config --list`` lower-cases the
            section and key and keeps a subsection's case, so the
            comparison is exact.

    Returns:
        `bool`:
            ``True`` when the entry is copied into the shadow.
    """
    if name in _COPIED_KEYS:
        return True
    section, _, rest = name.partition(".")
    subsection, dot, key = rest.rpartition(".")
    return bool(dot and subsection) and (
        (section, key) in _COPIED_SUBSECTION_KEYS
    )


def _render_config(
    entries: list[tuple[str, str | None]],
) -> bytes | None:
    """Serialise allow-listed entries back into git config syntax.

    Every value is written double-quoted with git's escapes, so nothing
    in a value — a ``;``, a ``#``, a trailing backslash — can change how
    the line parses.

    Args:
        entries (`list[tuple[str, str | None]]`):
            ``(name, value)`` pairs from :func:`_read_config_entries`;
            a ``None`` value is written as a bare key.

    Returns:
        `bytes | None`:
            The config file, or ``None`` when an entry holds a character
            git config syntax cannot carry.
    """
    sections: dict[tuple[str, str | None], list[str]] = {}
    for name, value in entries:
        section, _, rest = name.partition(".")
        subsection: str | None
        subsection, dot, key = rest.rpartition(".")
        if not dot:
            subsection = None
        if value is None:
            line = f"\t{key}\n"
        else:
            quoted = _quote_value(value)
            if quoted is None:
                logger.debug("config value of %s cannot be copied", name)
                return None
            line = f"\t{key} = {quoted}\n"
        sections.setdefault((section, subsection), []).append(line)

    lines: list[str] = []
    for (section, subsection), body in sections.items():
        if subsection is None:
            lines.append(f"[{section}]\n")
        else:
            quoted_sub = _quote_subsection(subsection)
            if quoted_sub is None:
                return None
            lines.append(f"[{section} {quoted_sub}]\n")
        lines.extend(body)
    return "".join(lines).encode("utf-8", errors="surrogateescape")


def _quote_value(value: str) -> str | None:
    """Quote a config value using the escapes git's parser understands.

    Args:
        value (`str`):
            The raw value.

    Returns:
        `str | None`:
            ``"…"`` with ``\\``, ``"``, newline and tab escaped, or
            ``None`` for any other control character.
    """
    if any(ord(char) < 0x20 and char not in "\t\n" for char in value):
        return None
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _quote_subsection(subsection: str) -> str | None:
    """Quote a subsection name such as a branch or remote.

    Args:
        subsection (`str`):
            The raw name.

    Returns:
        `str | None`:
            ``"…"`` with ``\\`` and ``"`` escaped, or ``None`` when it
            holds a newline or NUL, which git config cannot represent.
    """
    if "\n" in subsection or "\0" in subsection:
        return None
    escaped = subsection.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
