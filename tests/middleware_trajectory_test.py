# -*- coding: utf-8 -*-
"""Unit tests for TrajectoryMiddleware and RL/eval trajectory export."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, ContextConfig, InjectionConfig
from agentscope.event import (
    ExternalExecutionResultEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
)
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.middleware import (
    InMemoryTrajectorySink,
    JsonlTrajectorySink,
    RewardRecord,
    TrajectoryMiddleware,
    TrajectoryRecord,
    read_trajectories_jsonl,
)
from agentscope.model import ChatResponse, ChatUsage
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit


class _WeatherTool(ToolBase):
    """Stub weather tool returning deterministic weather output."""

    name: str = "get_weather"
    description: str = "Return stub weather for a city."
    input_schema: dict = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow tool execution without prompts."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def call(self, *args: Any, **kwargs: Any) -> ToolChunk:
        """Return stub weather."""
        del args
        await asyncio.sleep(0)
        city = str(kwargs.get("city", ""))
        return ToolChunk(
            content=[TextBlock(text=f"{city}: sunny, 25C")],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )


class _FailingTool(ToolBase):
    """Stub tool that always raises during execution."""

    name: str = "fail_op"
    description: str = "Always fail."
    input_schema: dict = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = True
    is_read_only: bool = True

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow invocation so execution failure path is tested."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def call(self, *args: Any, **kwargs: Any) -> ToolChunk:
        """Raise an error."""
        del args, kwargs
        raise RuntimeError("tool boom")


class _AskConfirmTool(_WeatherTool):
    """Tool requesting HITL user confirmation."""

    is_read_only: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Ask user confirmation."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="confirm required",
        )


class _ExternalTool(_WeatherTool):
    """Tool requiring external execution."""

    is_external_tool: bool = True


class _CompactingTool(ToolBase):
    """State-injected tool triggering Agent context compression mid-reply."""

    name: str = "trigger_compact"
    description: str = "Trigger context compaction inside an agent reply."
    input_schema: dict = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_state_injected: bool = True

    def __init__(self, agent_ref: list[Agent]) -> None:
        super().__init__()
        self._agent_ref = agent_ref

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow compaction helper tool."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def call(self, *args: Any, **kwargs: Any) -> ToolChunk:
        """Force compaction on the owning agent."""
        del args, kwargs
        agent = self._agent_ref[0]
        await agent.compress_context(
            context_config=ContextConfig(
                trigger_ratio=0.1,
                reserve_ratio=0.05,
                compression_tool_enabled=False,
            ),
        )
        return ToolChunk(
            content=[TextBlock(text="compacted")],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )


class TestTrajectoryMiddleware(IsolatedAsyncioTestCase):
    """Unit tests covering rollout capture, verifiers, sinks, and export."""

    async def _build_agent(
        self,
        responses: list[Any],
        *,
        middlewares: list[Any],
        tools: list[ToolBase] | None = None,
        context_size: int = 10000,
        context_config: ContextConfig | None = None,
        name: str = "traj_agent",
    ) -> Agent:
        model = MockModel(model="mock-gpt", context_size=context_size)
        model.set_responses(responses)
        toolkit = Toolkit()
        for tool in tools or []:
            await toolkit.add_tool(tool)
        return Agent(
            name=name,
            system_prompt="You are helpful.",
            model=model,
            toolkit=toolkit,
            middlewares=middlewares,
            context_config=context_config,
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

    async def test_multi_step_tool_rollout_and_openai_export(self) -> None:
        """Capture multi-step tool loop, usage totals, and OpenAI export."""
        mw = TrajectoryMiddleware(capture_tools=True)
        agent = await self._build_agent(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_1",
                            name="get_weather",
                            input='{"city": "Hangzhou"}',
                        ),
                    ],
                    is_last=True,
                    usage=ChatUsage(
                        input_tokens=10,
                        output_tokens=5,
                        cache_input_tokens=4,
                        time=0.2,
                    ),
                ),
                ChatResponse(
                    content=[TextBlock(text="Hangzhou is sunny.")],
                    is_last=True,
                    usage=ChatUsage(
                        input_tokens=20,
                        output_tokens=8,
                        cache_creation_input_tokens=3,
                        time=0.3,
                    ),
                ),
            ],
            middlewares=[mw],
            tools=[_WeatherTool()],
        )

        msg = await agent.reply(UserMsg("user", "Weather in Hangzhou?"))
        self.assertEqual(
            msg.get_content_blocks("text")[0].text, "Hangzhou is sunny."
        )
        self.assertEqual(len(mw.trajectories), 1)
        traj = mw.trajectories[0]
        self.assertIs(mw.last_trajectory, traj)
        self.assertEqual(traj.status, "completed")
        self.assertEqual(len(traj.steps), 2)
        self.assertEqual(traj.steps[0].model_name, "mock-gpt")
        self.assertIsNotNone(traj.steps[0].tools)
        self.assertEqual(len(traj.steps[0].tool_executions), 1)
        tool_rec = traj.steps[0].tool_executions[0]
        self.assertEqual(tool_rec.tool_name, "get_weather")
        self.assertEqual(tool_rec.tool_input, {"city": "Hangzhou"})
        self.assertEqual(tool_rec.state, "success")
        self.assertFalse(tool_rec.is_error)

        self.assertEqual(traj.total_usage.input_tokens, 30)
        self.assertEqual(traj.total_usage.output_tokens, 13)
        self.assertEqual(traj.total_usage.cache_input_tokens, 4)
        self.assertEqual(traj.total_usage.cache_creation_input_tokens, 3)

        exported = traj.to_openai_messages()
        roles = [m["role"] for m in exported]
        self.assertEqual(
            roles, ["system", "user", "assistant", "tool", "assistant"]
        )
        self.assertEqual(exported[2]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(exported[3]["tool_call_id"], "call_1")
        self.assertIn("25C", exported[3]["content"])
        self.assertEqual(exported[4]["content"], "Hangzhou is sunny.")

    async def test_streaming_response_and_tool_error_capture(self) -> None:
        """Capture streamed chunks and failed tool calls accurately."""
        mw = TrajectoryMiddleware()
        agent = await self._build_agent(
            [
                [
                    ChatResponse(
                        content=[TextBlock(text="Calling...")],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[
                            TextBlock(text="Calling..."),
                            ToolCallBlock(
                                id="call_err",
                                name="fail_op",
                                input="{}",
                            ),
                        ],
                        is_last=True,
                        usage=ChatUsage(
                            input_tokens=12, output_tokens=6, time=0.1
                        ),
                    ),
                ],
                [
                    ChatResponse(
                        content=[TextBlock(text="Recovered after failure.")],
                        is_last=True,
                        usage=ChatUsage(
                            input_tokens=18, output_tokens=4, time=0.1
                        ),
                    ),
                ],
            ],
            middlewares=[mw],
            tools=[_FailingTool()],
        )

        await agent.reply(UserMsg("user", "run failing tool"))
        traj = mw.trajectories[0]
        self.assertEqual(len(traj.steps), 2)
        self.assertEqual(traj.steps[0].usage.input_tokens, 12)
        self.assertEqual(len(traj.steps[0].tool_executions), 1)
        failed_exec = traj.steps[0].tool_executions[0]
        self.assertTrue(failed_exec.is_error)
        self.assertEqual(failed_exec.state, "error")

    async def test_compaction_splits_training_segments(self) -> None:
        """Record compaction boundaries and emit separate RL segments."""
        mw = TrajectoryMiddleware()
        agent_ref: list[Agent] = []
        compact_tool = _CompactingTool(agent_ref)

        # Responses consumed by _call_api:
        # 1) Reasoning calls trigger_compact tool
        # 2) Reasoning after compaction returns final text answer
        agent = await self._build_agent(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="c_cmp",
                            name="trigger_compact",
                            input="{}",
                        ),
                    ],
                    is_last=True,
                    usage=ChatUsage(
                        input_tokens=40,
                        output_tokens=5,
                        time=0.1,
                    ),
                ),
                ChatResponse(
                    content=[TextBlock(text="Final answer post-compact.")],
                    is_last=True,
                    usage=ChatUsage(
                        input_tokens=20,
                        output_tokens=7,
                        time=0.1,
                    ),
                ),
            ],
            middlewares=[mw],
            tools=[compact_tool],
            context_size=1000,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.05,
                compression_tool_enabled=False,
            ),
        )
        agent_ref.append(agent)

        # Seed prior message so compaction visibly reduces context count.
        await agent.observe(UserMsg("user", "earlier context " * 80))
        await agent.reply(UserMsg("user", "compact and finish"))

        traj = mw.trajectories[0]
        self.assertEqual(len(traj.compactions), 1)
        comp = traj.compactions[0]
        self.assertEqual(comp.segment_before, 0)
        self.assertEqual(comp.segment_after, 1)
        self.assertGreater(comp.before_message_count, comp.after_message_count)

        segments = traj.to_training_segments()
        self.assertEqual([s["segment_index"] for s in segments], [0, 1])
        self.assertEqual(
            segments[1]["messages"][-1]["content"],
            "Final answer post-compact.",
        )

    async def test_verifiers_sync_async_and_error_isolation(self) -> None:
        """Evaluate sync/async verifiers and isolate or raise verifier bugs."""

        async def async_verifier(traj: TrajectoryRecord) -> RewardRecord:
            await asyncio.sleep(0)
            return RewardRecord(
                name="completion",
                score=1.0 if traj.status == "completed" else 0.0,
                reason="ok",
            )

        def broken_verifier(_traj: TrajectoryRecord) -> float:
            raise ValueError("verifier crashed")

        mw = TrajectoryMiddleware(
            verifiers=[
                lambda traj: float(len(traj.steps)),
                async_verifier,
                broken_verifier,
            ],
        )
        agent = await self._build_agent(
            [ChatResponse(content=[TextBlock(text="done")], is_last=True)],
            middlewares=[mw],
        )
        await agent.reply(UserMsg("user", "hi"))

        traj = mw.trajectories[0]
        self.assertEqual(len(traj.rewards), 2)
        self.assertEqual(traj.total_reward, 2.0)
        self.assertEqual(len(traj.metadata["verifier_errors"]), 1)
        self.assertEqual(
            traj.metadata["verifier_errors"][0]["verifier"],
            "broken_verifier",
        )

        strict_mw = TrajectoryMiddleware(
            verifiers=[broken_verifier],
            raise_on_verifier_error=True,
        )
        strict_agent = await self._build_agent(
            [ChatResponse(content=[TextBlock(text="done")], is_last=True)],
            middlewares=[strict_mw],
        )
        with self.assertRaises(ValueError):
            await strict_agent.reply(UserMsg("user", "hi"))

    async def test_jsonl_sink_nan_safety_and_corrupt_tail(self) -> None:
        """Persist RFC-8259 JSONL with NaN safety and torn tail recovery."""
        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "nested" / "rollouts.jsonl"
            sink = JsonlTrajectorySink(out_path)
            mem_sink = InMemoryTrajectorySink()
            mw = TrajectoryMiddleware(
                sinks=[sink, mem_sink],
                verifiers=[lambda _t: 0.75],
                metadata={"nan_val": float("nan"), "inf_val": float("inf")},
            )
            agent = await self._build_agent(
                [ChatResponse(content=[TextBlock(text="ok")], is_last=True)],
                middlewares=[mw],
            )
            await agent.reply(UserMsg("user", "hello"))

            raw_text = out_path.read_text(encoding="utf-8")
            self.assertNotIn("NaN", raw_text)
            self.assertNotIn("Infinity", raw_text)

            # Append a truncated final line to simulate crash during flush.
            with out_path.open("a", encoding="utf-8") as f:
                f.write('{"trajectory_id": "partial\n')

            loaded = read_trajectories_jsonl(out_path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].total_reward, 0.75)
            self.assertIsNone(loaded[0].metadata["nan_val"])
            self.assertIsNone(loaded[0].metadata["inf_val"])
            self.assertEqual(
                loaded[0].to_dict(), mem_sink.trajectories[0].to_dict()
            )

            with self.assertRaises(json.JSONDecodeError):
                read_trajectories_jsonl(out_path, skip_corrupt_last_line=False)

    async def test_concurrent_isolation_and_cross_task_close(self) -> None:
        """Keep concurrent ContextVar traces isolated and close cleanly."""
        mw = TrajectoryMiddleware()
        agent1 = await self._build_agent(
            [ChatResponse(content=[TextBlock(text="from_a1")], is_last=True)],
            middlewares=[mw],
            name="agent_one",
        )
        agent2 = await self._build_agent(
            [ChatResponse(content=[TextBlock(text="from_a2")], is_last=True)],
            middlewares=[mw],
            name="agent_two",
        )

        await asyncio.gather(
            agent1.reply(UserMsg("user", "q1")),
            agent2.reply(UserMsg("user", "q2")),
        )
        self.assertEqual(len(mw.trajectories), 2)
        by_agent = {t.agent_name: t for t in mw.trajectories}
        self.assertEqual(
            by_agent["agent_one"].input_messages[0]["content"][0]["text"],
            "q1",
        )
        self.assertEqual(
            by_agent["agent_two"].input_messages[0]["content"][0]["text"],
            "q2",
        )

        # Cross-task aclose on reply_stream must not raise ContextVar errors.
        slow_mw = TrajectoryMiddleware()
        slow_agent = await self._build_agent(
            [ChatResponse(content=[TextBlock(text="streamed")], is_last=True)],
            middlewares=[slow_mw],
        )
        stream = slow_agent.reply_stream(UserMsg("user", "abort me"))
        await anext(stream)
        await asyncio.create_task(stream.aclose())
        self.assertEqual(len(slow_mw.trajectories), 1)
        self.assertEqual(slow_mw.trajectories[0].status, "interrupted")

    async def test_hitl_external_error_and_disabled_bypass(self) -> None:
        """Record HITL, external execution, error, and disabled modes."""
        hitl_mw = TrajectoryMiddleware()
        hitl_agent = await self._build_agent(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_hitl",
                            name="get_weather",
                            input='{"city": "Beijing"}',
                        ),
                    ],
                    is_last=True,
                ),
            ],
            middlewares=[hitl_mw],
            tools=[_AskConfirmTool()],
        )
        events = [
            ev
            async for ev in hitl_agent.reply_stream(
                UserMsg("user", "Beijing weather?"),
            )
        ]
        self.assertTrue(
            any(isinstance(e, RequireUserConfirmEvent) for e in events),
        )
        self.assertEqual(hitl_mw.trajectories[0].status, "paused_hitl")

        ext_mw = TrajectoryMiddleware()
        ext_agent = await self._build_agent(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_ext",
                            name="get_weather",
                            input='{"city": "Tokyo"}',
                        ),
                    ],
                    is_last=True,
                ),
                ChatResponse(
                    content=[TextBlock(text="Tokyo is clear.")],
                    is_last=True,
                ),
            ],
            middlewares=[ext_mw],
            tools=[_ExternalTool()],
        )
        ext_events = [
            ev
            async for ev in ext_agent.reply_stream(
                UserMsg("user", "Tokyo weather?"),
            )
        ]
        self.assertTrue(
            any(
                isinstance(e, RequireExternalExecutionEvent)
                for e in ext_events
            ),
        )
        self.assertEqual(ext_mw.trajectories[0].status, "paused_external")

        await ext_agent.reply(
            ExternalExecutionResultEvent(
                reply_id=ext_mw.trajectories[0].reply_id,
                execution_results=[
                    ToolResultBlock(
                        id="call_ext",
                        name="get_weather",
                        output="Tokyo: clear, 20C",
                        state=ToolResultState.SUCCESS,
                    ),
                ],
            ),
        )
        resumed = ext_mw.trajectories[1]
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(
            resumed.metadata["unattached_tool_executions"][0]["is_external"],
            True,
        )

        err_mw = TrajectoryMiddleware()
        err_agent = await self._build_agent(
            [RuntimeError("model down")],
            middlewares=[err_mw],
        )
        with self.assertRaises(RuntimeError):
            await err_agent.reply(UserMsg("user", "fail"))
        self.assertEqual(err_mw.trajectories[0].status, "error")
        self.assertIn("model down", err_mw.trajectories[0].error or "")

        off_mw = TrajectoryMiddleware(enabled=False)
        off_agent = await self._build_agent(
            [ChatResponse(content=[TextBlock(text="ok")], is_last=True)],
            middlewares=[off_mw],
        )
        await off_agent.reply(UserMsg("user", "bypass"))
        self.assertEqual(off_mw.trajectories, [])
