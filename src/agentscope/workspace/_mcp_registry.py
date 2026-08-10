# -*- coding: utf-8 -*-
"""Per-scope MCP registry shared by every workspace implementation.

A workspace serves many agents and many sessions, and they must not
share MCP state: a browser MCP logged in for one session must not be
visible to another. :class:`MCPRegistryMixin` gives
:class:`~agentscope.workspace.WorkspaceBase` that isolation, split
into two layers:

* **Declarations** — ``{scope: [MCPClient spec, ...]}``, persisted to
  ``${workdir}/.mcp``. A scope absent from the file has never been
  modified and inherits ``default_mcps``; a scope stored with an empty
  list explicitly has no MCPs. The two are distinct states.
* **Instances** — ``{scope: {name: live client}}``, built lazily on the
  first :meth:`MCPRegistryMixin.list_mcps` for a scope and never
  persisted. Nothing connects at workspace startup, so boot cost does
  not grow with the number of agents or sessions ever seen.

Live *stateful* instances (stdio subprocesses, long-lived HTTP
sessions) are capped by ``max_live_stateful_mcps`` and reclaimed
least-recently-used; because instantiation is lazy, eviction is
self-healing — the next ``list_mcps`` rebuilds from the declaration.
Stateless clients hold no connection at all, so they are exempt.

Subclasses provide :meth:`MCPRegistryMixin._new_mcp_instance` — the one
place local and gateway-routed workspaces differ.
"""

import asyncio
import json
import time
from abc import abstractmethod
from typing import TypeAlias

from .._logging import logger
from ..mcp import MCPClient
from ..tool import BackendBase

#: Scope an MCP belongs to: ``(agent_id, session_id)``. ``("", "")`` is
#: the legacy scope used by callers that pass neither id.
MCPKey: TypeAlias = tuple[str, str]

#: Current ``.mcp`` schema version. Version 1 was a bare JSON list of
#: specs with no scoping; it is read back into the legacy scope.
MCP_FILE_VERSION = 2

_LEGACY_SCOPE: MCPKey = ("", "")

_DEFAULT_MAX_LIVE_STATEFUL_MCPS = 40


class MCPRegistryMixin:
    """Scope-aware MCP declarations, instances and persistence.

    Mixed into :class:`~agentscope.workspace.WorkspaceBase`, which owns
    the attributes declared here and supplies ``default_mcps``,
    ``is_persistent``, ``_backend`` and ``_mcp_file``.
    """

    # Supplied by the host class: ``default_mcps`` and ``_backend`` are
    # plain attributes, ``is_persistent`` and ``_mcp_file`` properties.
    default_mcps: list[MCPClient]
    _backend: BackendBase | None

    max_live_stateful_mcps: int
    """Upper bound on live stateful MCP instances retained for
    *other* scopes. See :meth:`_enforce_mcp_capacity`."""

    _mcp_specs: dict[MCPKey, list[MCPClient]]
    """Declared MCP configs per scope — the persisted layer.

    A missing key means "this scope has never diverged from
    :attr:`default_mcps`"; an empty list means "this scope explicitly
    has no MCPs". The two are *not* interchangeable.
    """

    _mcp_instances: dict[MCPKey, dict[str, MCPClient]]
    """Live MCP handles per scope, ``{scope: {name: client}}`` — the
    runtime layer, built lazily and never persisted."""

    _mcp_last_used: dict[MCPKey, float]
    """Monotonic timestamp of the last :meth:`list_mcps` per scope,
    driving LRU eviction. Turn-level granularity — individual tool
    calls do not pass through the workspace."""

    _mcp_lock: asyncio.Lock
    """Guards mutation of the MCP dicts and the ``.mcp`` file."""

    def _init_mcp_registry(self, max_live_stateful_mcps: int | None) -> None:
        """Initialise the registry. Called from ``WorkspaceBase``.

        Args:
            max_live_stateful_mcps (`int | None`):
                Cap on concurrently live stateful instances. ``None``
                derives ``max(40, 2 * <stateful defaults>)`` so a scope
                can always instantiate its own seeded set.
        """
        self.max_live_stateful_mcps = max_live_stateful_mcps or max(
            _DEFAULT_MAX_LIVE_STATEFUL_MCPS,
            2 * len([m for m in self.default_mcps if m.is_stateful]),
        )
        self._mcp_specs = {}
        self._mcp_instances = {}
        self._mcp_last_used = {}
        self._mcp_lock = asyncio.Lock()

    @staticmethod
    def _scope(agent_id: str | None, session_id: str | None) -> MCPKey:
        """Normalise a caller-supplied pair into an :data:`MCPKey`.

        ``None`` maps to ``""``, so pre-scoping callers that pass
        neither id all share the legacy ``("", "")`` scope.
        """
        return (agent_id or "", session_id or "")

    def _declared_specs(self, scope: MCPKey) -> list[MCPClient]:
        """Configs declared for ``scope``, seeded from defaults.

        A scope absent from :attr:`_mcp_specs` has never been
        modified, so it inherits fresh copies of
        :attr:`default_mcps`; nothing is persisted for it until it
        actually diverges. An empty list is therefore *not* the same
        as a missing key — it means the scope explicitly has none.
        """
        declared = self._mcp_specs.get(scope)
        if declared is not None:
            return declared
        return [
            MCPClient.model_validate(m.model_dump(mode="json"))
            for m in self.default_mcps
        ]

    @abstractmethod
    async def _new_mcp_instance(
        self,
        scope: MCPKey,
        spec: MCPClient,
    ) -> MCPClient:
        """Build and connect one live MCP handle for ``scope``.

        :class:`LocalWorkspace` returns a connected local client;
        :class:`SandboxedWorkspaceBase` returns a gateway-wired
        proxy. Raising propagates to :meth:`list_mcps`, which logs
        and skips the entry.

        Args:
            scope (`MCPKey`):
                The ``(agent_id, session_id)`` owning the instance.
            spec (`MCPClient`):
                The declared config to instantiate from.
        """

    async def list_mcps(
        self,
        agent_id: str | None = None,
        session_id: str | None = None,
        *,
        instantiate: bool = True,
    ) -> list[MCPClient]:
        """Return the MCP clients for one ``(agent_id, session_id)``.

        Instances are built lazily: nothing is connected until a
        scope first asks for its MCPs, and each scope gets its own
        instances so stateful sessions (browser cookies, login state)
        never leak across agents or sessions. Instances previously
        dropped by :meth:`_enforce_mcp_capacity` are rebuilt here.

        Args:
            agent_id (`str | None`, optional):
                The owning agent. ``None`` selects the legacy scope.
            session_id (`str | None`, optional):
                The owning session. ``None`` selects the legacy scope.
            instantiate (`bool`, defaults to `True`):
                When ``False``, return the declared configs without
                connecting anything — for callers that only need to
                enumerate, e.g. session teardown.
        """
        scope = self._scope(agent_id, session_id)
        if not instantiate:
            return list(self._declared_specs(scope))

        async with self._mcp_lock:
            self._mcp_last_used[scope] = time.monotonic()
            live = self._mcp_instances.setdefault(scope, {})
            specs = self._declared_specs(scope)
            for spec in specs:
                if spec.name in live:
                    continue
                await self._enforce_mcp_capacity(scope, spec)
                try:
                    live[spec.name] = await self._new_mcp_instance(scope, spec)
                except Exception as e:
                    logger.warning(
                        "Failed to start MCP %r for %s: %s, skipping.",
                        spec.name,
                        scope,
                        e,
                    )
            # Declaration order, not instantiation order — an MCP
            # rebuilt after eviction must not jump to the end.
            return [live[s.name] for s in specs if s.name in live]

    # ── for User: dynamic MCP management ───────────────────────────

    async def add_mcp(
        self,
        mcp_client: MCPClient,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Register a new MCP server for one scope and persist it.

        Args:
            mcp_client (`MCPClient`):
                The MCP to register.
            agent_id (`str | None`, optional):
                The owning agent. ``None`` selects the legacy scope.
            session_id (`str | None`, optional):
                The owning session. ``None`` selects the legacy scope.

        Raises:
            `ValueError`:
                If an MCP with the same name already exists in this
                scope.
        """
        scope = self._scope(agent_id, session_id)
        async with self._mcp_lock:
            specs = self._declared_specs(scope)
            if any(m.name == mcp_client.name for m in specs):
                raise ValueError(
                    f"MCP {mcp_client.name!r} already exists for "
                    f"agent={scope[0]!r} session={scope[1]!r}.",
                )
            live = self._mcp_instances.setdefault(scope, {})
            await self._enforce_mcp_capacity(scope, mcp_client)
            live[mcp_client.name] = await self._new_mcp_instance(
                scope,
                mcp_client,
            )
            # Materialise the full list on first divergence so the
            # persisted copy is self-contained.
            self._mcp_specs[scope] = [*specs, mcp_client]
            await self._save_mcp_file()

    async def remove_mcp(
        self,
        name: str,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Deregister an MCP by name within one scope.

        Only this scope's declaration is touched — other agents and
        sessions keep their own copies, and their instances are
        independent, so there is nothing to reconcile across scopes.

        Args:
            name (`str`):
                MCP name to remove. Unknown names log a warning and
                return silently.
            agent_id (`str | None`, optional):
                The owning agent. ``None`` selects the legacy scope.
            session_id (`str | None`, optional):
                The owning session. ``None`` selects the legacy scope.
        """
        scope = self._scope(agent_id, session_id)
        async with self._mcp_lock:
            specs = self._declared_specs(scope)
            if not any(m.name == name for m in specs):
                logger.warning(
                    "MCP %r not found for agent=%r session=%r",
                    name,
                    scope[0],
                    scope[1],
                )
                return
            instance = self._mcp_instances.get(scope, {}).pop(name, None)
            if instance is not None:
                await self._close_mcp_instance(instance)
            self._mcp_specs[scope] = [m for m in specs if m.name != name]
            await self._save_mcp_file()

    # ── instance lifecycle ─────────────────────────────────────────

    @staticmethod
    async def _close_mcp_instance(instance: MCPClient) -> None:
        """Close one live handle, downgrading failures to warnings.

        Stateless clients hold no connection, so closing them is a
        no-op — they are skipped rather than round-tripped.
        """
        if not (instance.is_stateful and instance.is_connected):
            return
        try:
            await instance.close()
        except Exception as e:
            logger.warning("MCP %r close failed: %s", instance.name, e)

    async def _enforce_mcp_capacity(
        self,
        scope: MCPKey,
        incoming: MCPClient,
    ) -> None:
        """Make room for ``incoming`` under
        :attr:`max_live_stateful_mcps`.

        Only stateful instances count: a stateless client holds no
        connection or subprocess, so capping them would reclaim
        nothing.

        Eviction never targets ``scope`` itself, so a scope always
        gets its full set instantiated no matter how the cap is
        configured. When every live instance belongs to ``scope`` the
        cap is exceeded rather than the request being broken.

        Callers must hold :attr:`_mcp_lock`.
        """
        if not incoming.is_stateful:
            return
        while self._count_live_stateful() >= self.max_live_stateful_mcps:
            victim_scope = min(
                (
                    s
                    for s, by_name in self._mcp_instances.items()
                    if s != scope
                    and any(c.is_stateful for c in by_name.values())
                ),
                key=lambda s: self._mcp_last_used.get(s, 0.0),
                default=None,
            )
            if victim_scope is None:
                logger.warning(
                    "All %d live stateful MCPs belong to agent=%r "
                    "session=%r; exceeding max_live_stateful_mcps to "
                    "start %r.",
                    self._count_live_stateful(),
                    scope[0],
                    scope[1],
                    incoming.name,
                )
                return
            by_name = self._mcp_instances[victim_scope]
            name = next(n for n, c in by_name.items() if c.is_stateful)
            logger.info(
                "Evicting idle MCP %r (agent=%r session=%r) to stay "
                "under max_live_stateful_mcps=%d.",
                name,
                victim_scope[0],
                victim_scope[1],
                self.max_live_stateful_mcps,
            )
            await self._close_mcp_instance(by_name.pop(name))

    def _count_live_stateful(self) -> int:
        """Number of live stateful instances across every scope."""
        return sum(
            1
            for by_name in self._mcp_instances.values()
            for c in by_name.values()
            if c.is_stateful
        )

    async def _close_all_mcp_instances(self) -> None:
        """Close every live handle and drop the instance layer.

        Declarations in :attr:`_mcp_specs` are left intact — a later
        :meth:`list_mcps` rebuilds from them.
        """
        for by_name in self._mcp_instances.values():
            for instance in list(by_name.values()):
                await self._close_mcp_instance(instance)
        self._mcp_instances.clear()
        self._mcp_last_used.clear()

    # ── MCP persistence (shared) ───────────────────────────────────

    def _serialise_mcp_specs(self) -> bytes:
        """Render :attr:`_mcp_specs` as a ``.mcp`` v2 payload.

        Scopes are nested ``{agent_id: {session_id: [spec, ...]}}``
        rather than joined into a composite key, so no separator can
        collide with an id. The legacy scope round-trips as the
        ``""`` / ``""`` pair.
        """
        mcps: dict[str, dict[str, list[dict]]] = {}
        for (agent_id, session_id), specs in self._mcp_specs.items():
            mcps.setdefault(agent_id, {})[session_id] = [
                m.model_dump(mode="json") for m in specs
            ]
        return json.dumps(
            {
                "version": MCP_FILE_VERSION,
                "mcps": mcps,
            },
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8")

    async def _save_mcp_file(self) -> None:
        """Persist :attr:`_mcp_specs` to ``${workdir}/.mcp``.

        Only *declarations* are written, and only for scopes that
        diverged from :attr:`default_mcps` — an untouched scope leaves
        no trace, so the file does not grow with session count on its
        own. Live instances are never persisted.

        No-op when :attr:`is_persistent` is ``False`` (e.g. ephemeral
        Docker container without a host bind-mount). Failures are
        logged but not raised — the in-memory copy stays authoritative
        regardless of whether disk persistence succeeded.

        Callers are expected to hold :attr:`_mcp_lock` already.
        """
        if not self.is_persistent:
            return
        backend = self._backend
        if backend is None:
            return
        try:
            await backend.write_file(
                self._mcp_file,
                self._serialise_mcp_specs(),
            )
        except Exception as e:
            logger.warning(
                "Failed to save MCP file at %s: %s",
                self._mcp_file,
                e,
            )

    async def _restore_mcp_specs(self) -> dict[MCPKey, list[MCPClient]]:
        """Read the declarations persisted in ``${workdir}/.mcp``.

        Returns an empty mapping when the file is absent, unreadable
        or unparseable — every scope then falls back to
        :attr:`default_mcps`, so a corrupted file cannot block
        startup. Version 1 files (a bare JSON list) are read into the
        legacy scope; the next write emits v2.
        """
        if not self.is_persistent:
            return {}
        backend = self._backend
        if backend is None:
            return {}
        try:
            if not await backend.file_exists(self._mcp_file):
                return {}
            raw = await backend.read_file(self._mcp_file)
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            logger.warning(
                "Failed to read MCP file at %s, falling back to "
                "default_mcps: %s",
                self._mcp_file,
                e,
            )
            return {}

        def _parse(cfgs: list, scope: MCPKey) -> list[MCPClient]:
            """Parse serialised configs, skipping invalid entries."""
            parsed: list[MCPClient] = []
            for m in cfgs:
                try:
                    parsed.append(MCPClient.model_validate(m))
                except Exception as e:
                    name = m.get("name", "?") if isinstance(m, dict) else "?"
                    logger.warning(
                        "Skipping invalid MCP entry %r in scope %s: %s",
                        name,
                        scope,
                        e,
                    )
            return parsed

        if isinstance(data, list):
            logger.info("Migrating .mcp v1 (flat list) into the legacy scope.")
            return {_LEGACY_SCOPE: _parse(data, _LEGACY_SCOPE)}

        if not isinstance(data, dict):
            logger.warning(
                "%s is neither a list nor an object; ignoring it.",
                self._mcp_file,
            )
            return {}

        specs: dict[MCPKey, list[MCPClient]] = {}
        for agent_id, by_session in (data.get("mcps") or {}).items():
            if not isinstance(by_session, dict):
                continue
            for session_id, cfgs in by_session.items():
                if isinstance(cfgs, list):
                    scope = (agent_id, session_id)
                    specs[scope] = _parse(cfgs, scope)
        return specs
