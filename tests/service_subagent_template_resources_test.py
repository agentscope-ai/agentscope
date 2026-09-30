# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for a :class:`SubAgentTemplate` equipping its workers with its
own MCPs and skills.

A worker shares the leader's ``workspace_id`` but has its own agent id,
so the resources land in the worker's own MCP slot and skill partition.
The tests below pin that isolation, the reclaim on delete, and that
one unusable entry costs the worker only that entry.
"""
import io
import os
import tarfile
import tempfile
from contextlib import AsyncExitStack
from typing import AsyncIterator
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis

from utils import AnyString, AnyValue

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import SessionService
from agentscope.app._tool import AgentCreate, TeamCreate
from agentscope.app._types import SubAgentTemplate
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    RedisStorage,
    SessionConfig,
)
from agentscope.app.storage._model._team import TeamMember
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.mcp import MCPClient
from agentscope.skill import SkillArchive, SkillSourceBase
from agentscope.tool import ToolChunk
from agentscope.workspace import WorkspaceBase


def _make_storage(fr: fakeredis.aioredis.FakeRedis) -> RedisStorage:
    """Construct a :class:`RedisStorage` that talks to *fr*."""

    class _S(RedisStorage):
        async def __aenter__(self) -> "RedisStorage":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _S()


def _make_bus(fr: fakeredis.aioredis.FakeRedis) -> RedisMessageBus:
    """Construct a :class:`RedisMessageBus` that talks to *fr*."""

    class _B(RedisMessageBus):
        async def __aenter__(  # type: ignore[override]
            self,
        ) -> "RedisMessageBus":
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _B()


def _write_skill(root: str, dir_name: str, name: str) -> str:
    """Write a minimal skill directory and return its path."""
    path = os.path.join(root, dir_name)
    os.makedirs(path)
    with open(os.path.join(path, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\nname: {name}\ndescription: a test skill\n---\nbody")
    return path


class _TarSkillSource(SkillSourceBase):
    """A source serving one skill as a tar built in memory."""

    async def open(self) -> SkillArchive:
        """Return a fresh archive, as a reusable source must."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            body = (
                f"---\nname: {self.name}\ndescription: a test skill"
                f"\n---\nbody"
            ).encode("utf-8")
            info = tarfile.TarInfo(name=f"{self.name}/SKILL.md")
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))

        async def _chunks() -> AsyncIterator[bytes]:
            data = buf.getvalue()
            for i in range(0, len(data), 11):
                yield data[i : i + 11]

        return SkillArchive("tar", _chunks())


class _TemplateResourcesTestBase(IsolatedAsyncioTestCase):
    """A leader that already owns a team, plus a real local workspace."""

    user_id = "u"
    chunk: ToolChunk
    worker: TeamMember
    workspace: WorkspaceBase

    async def asyncSetUp(self) -> None:
        # enterContext is the unittest equivalent of "with", which
        # pylint does not recognize.
        # pylint: disable=consider-using-with
        self.skills_root = self.enterContext(tempfile.TemporaryDirectory())
        basedir = self.enterContext(tempfile.TemporaryDirectory())

        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.storage = await self._stack.enter_async_context(
            _make_storage(self.fr),
        )
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))
        self.workspace_manager = LocalWorkspaceManager(basedir)
        self.addAsyncCleanup(self.workspace_manager.close_all)

        self.leader_agent = AgentRecord(
            user_id=self.user_id,
            source="user",
            data=AgentData(
                name="leader",
                system_prompt="You are leader.",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        await self.storage.upsert_agent(self.user_id, self.leader_agent)
        self.leader_session = await self.storage.upsert_session(
            user_id=self.user_id,
            agent_id=self.leader_agent.id,
            config=SessionConfig(workspace_id="ws"),
        )
        await TeamCreate(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )(name="alpha", description="t-desc")

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def _create_worker(self, template: SubAgentTemplate) -> None:
        """Spawn one worker from *template* and record its identity."""
        self.chunk = await AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=self.workspace_manager,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            sub_agent_templates={template.type: template},
        )(
            name="worker",
            description="does research",
            prompt="please look up X",
            subagent_type=template.type,
        )
        session = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, session.team_id)
        self.worker = team.data.members[0]
        self.workspace = await self.workspace_manager.get_workspace(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
            "ws",
        )


class TestSubAgentTemplateResources(_TemplateResourcesTestBase):
    """A template's MCPs and skills reach its worker and nobody else."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self._create_worker(
            SubAgentTemplate(
                type="researcher",
                description="Looks things up.",
                system_prompt_template="You are {member_name}.",
                mcps=[
                    MCPClient(
                        name="tavily",
                        is_stateful=False,
                        mcp_config={
                            "type": "http_mcp",
                            "url": "https://example.invalid/mcp",
                        },
                    ),
                ],
                skills=[
                    _write_skill(self.skills_root, "lit-review", "alpha"),
                    _TarSkillSource("beta"),
                ],
            ),
        )

    async def test_the_worker_gets_the_template_mcps(self) -> None:
        """The template's MCP is declared under the worker's own slot."""
        mcps = await self.workspace.list_mcps(
            agent_id=self.worker.agent_id,
            session_id=self.worker.session_id,
        )
        self.assertListEqual(
            [_.model_dump(mode="json") for _ in mcps],
            [
                {
                    "name": "tavily",
                    "is_stateful": False,
                    "mcp_config": {
                        "type": "http_mcp",
                        "url": "https://example.invalid/mcp",
                        "headers": None,
                        "timeout": 30.0,
                    },
                    "enable_tools": None,
                    "disable_tools": None,
                    "execution_timeout": None,
                },
            ],
        )

    async def test_the_worker_gets_the_template_skills(self) -> None:
        """A local directory and an archive source both land."""
        skills = await self.workspace.list_skills(
            agent_id=self.worker.agent_id,
        )
        self.assertListEqual(
            sorted((vars(_) for _ in skills), key=lambda _: _["name"]),
            [
                {
                    "name": "alpha",
                    "description": "a test skill",
                    "dir": AnyString(),
                    "markdown": "body",
                    "updated_at": AnyValue(),
                },
                {
                    "name": "beta",
                    "description": "a test skill",
                    "dir": AnyString(),
                    "markdown": "body",
                    "updated_at": AnyValue(),
                },
            ],
        )

    async def test_the_leader_is_left_alone(self) -> None:
        """The leader shares the workspace but not the worker's tools."""
        self.assertListEqual(
            [
                await self.workspace.list_mcps(
                    agent_id=self.leader_agent.id,
                    session_id=self.leader_session.id,
                ),
                await self.workspace.list_skills(
                    agent_id=self.leader_agent.id,
                ),
            ],
            [[], []],
        )

    async def test_deleting_the_worker_reclaims_them(self) -> None:
        """``delete_agent`` purges the worker's slot and partition."""
        await SessionService(
            self.storage,
            self.bus,
            self.workspace_manager,
        ).delete_agent(self.user_id, self.worker.agent_id)

        self.assertListEqual(
            [
                await self.workspace.list_mcps(
                    agent_id=self.worker.agent_id,
                    session_id=self.worker.session_id,
                ),
                await self.workspace.list_skills(
                    agent_id=self.worker.agent_id,
                ),
            ],
            [[], []],
        )


class TestSubAgentTemplateResourceFailures(_TemplateResourcesTestBase):
    """One unusable entry must not sink the rest of the creation."""

    async def test_an_unusable_skill_leaves_the_others_standing(
        self,
    ) -> None:
        """A directory holding no ``SKILL.md`` is dropped on its own —
        the worker keeps the rest and the tool still succeeds."""
        empty = os.path.join(self.skills_root, "empty")
        os.makedirs(empty)
        await self._create_worker(
            SubAgentTemplate(
                type="researcher",
                description="Looks things up.",
                system_prompt_template="You are {member_name}.",
                skills=[
                    _write_skill(self.skills_root, "lit-review", "alpha"),
                    empty,
                ],
            ),
        )

        skills = await self.workspace.list_skills(
            agent_id=self.worker.agent_id,
        )
        self.assertListEqual(
            [vars(_) for _ in skills],
            [
                {
                    "name": "alpha",
                    "description": "a test skill",
                    "dir": AnyString(),
                    "markdown": "body",
                    "updated_at": AnyValue(),
                },
            ],
        )
        self.assertDictEqual(
            self.chunk.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "text": AnyString(),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )
