# 第二十三章：造一个新 Agent 类型——Plan-Execute Agent

**难度**：高级

> 第四章我们追踪了 AgentBase 的继承体系，第五章分析了 ReActAgent 的推理-行动循环。本章你将从零写出一种全新的 Agent 类型——Plan-Execute Agent。它的核心思想是把"规划"和"执行"拆成两个独立阶段，让模型先制定计划再逐步执行，适合多步骤的复杂任务。写完之后，你会对 AgentBase 的 `reply` 生命周期、`_AgentMeta` 元类的 hook 机制、以及如何在框架中注册新 Agent 类型有完整的实战经验。

---

## 1. 实战目标

完成本章后，你将：

1. 理解 `AgentBase`（`_agent_base.py` 第 30-774 行）的完整生命周期：`__call__` -> `reply` -> `handle_interrupt`
2. 理解 `_AgentMeta`（`_agent_meta.py` 第 159-174 行）如何自动为 `reply`、`observe`、`print` 注入 hook
3. 子类化 `AgentBase`，实现一个两阶段（plan + execute）的 `PlanExecuteAgent`
4. 用 mocked model 编写纯逻辑的单元测试
5. 添加计划修订和错误恢复机制
6. 用真实的 Model 和 Toolkit 完成集成测试

---

## 2. 第一步：最小可用版本

### 2.1 AgentBase 生命周期速查

```
agent(msg)                  # __call__（第 448 行）
  ├── self._reply_id = uuid
  ├── reply_msg = await self.reply(msg)         # 第 455 行
  ├── except CancelledError:
  │   └── reply_msg = await self.handle_interrupt(msg)  # 第 459 行
  └── await self._broadcast_to_subscribers(reply_msg)   # 第 464 行
```

关键约束：

1. `reply` 是异步方法（第 197-203 行），签名 `async def reply(self, *args, **kwargs) -> Msg`
2. `observe` 用于接收但不回复消息（第 185-195 行）
3. `handle_interrupt` 在 `CancelledError` 时被调用（第 516-526 行）
4. `_AgentMeta`（`_agent_meta.py` 第 166-173 行）自动包装 `reply`、`observe`、`print`，注入 pre/post hook

### 2.2 核心思路

ReActAgent 的推理循环是 思考->行动->观察->再思考（`_react_agent.py` 第 432-518 行的 for 循环）。Plan-Execute Agent 把它拆成两个阶段：

1. **Plan 阶段**：一次性让模型生成结构化的步骤列表
2. **Execute 阶段**：逐步执行每个步骤，遇到问题可修订

适合长链条推理场景（数据分析管道、多步骤工作流），因为规划阶段能看到任务全貌。

### 2.3 最小实现

创建 `src/agentscope/agent/_plan_execute_agent.py`：

```python
# -*- coding: utf-8 -*-
"""Plan-Execute agent that separates planning from execution."""
import json
from typing import Any
from pydantic import BaseModel, Field
from ._agent_base import AgentBase
from .._logging import logger
from ..formatter import FormatterBase
from ..memory import MemoryBase, InMemoryMemory
from ..message import Msg, ToolUseBlock, ToolResultBlock
from ..model import ChatModelBase
from ..tool import Toolkit


class PlanStep(BaseModel):
    """A single step in the execution plan."""
    step_number: int = Field(description="Step number, starting from 1")
    description: str = Field(description="What to do in this step")
    tool_name: str | None = Field(default=None, description="Tool name or None")
    tool_args: dict[str, Any] | None = Field(default=None, description="Tool args")


class ExecutionPlan(BaseModel):
    """The structured plan from the planning phase."""
    steps: list[PlanStep] = Field(description="Ordered steps")
    goal: str = Field(description="The overall goal")


class PlanExecuteAgent(AgentBase):
    """An agent that separates planning from execution.

    Args:
        name (`str`): Agent name.
        sys_prompt (`str`): System prompt.
        model (`ChatModelBase`): Chat model.
        formatter (`FormatterBase`): Model formatter.
        toolkit (`Toolkit | None`): Tool functions.
        memory (`MemoryBase | None`): Memory storage.
        max_replans (`int`): Max plan revisions allowed.
    """
    def __init__(self, name: str, sys_prompt: str, model: ChatModelBase,
                 formatter: FormatterBase, toolkit: Toolkit | None = None,
                 memory: MemoryBase | None = None, max_replans: int = 2) -> None:
        super().__init__()
        self.name, self._sys_prompt = name, sys_prompt
        self.model, self.formatter = model, formatter
        self.toolkit = toolkit or Toolkit()
        self.memory = memory or InMemoryMemory()
        self.max_replans = max_replans
        self._plan: ExecutionPlan | None = None
        self._current_step = 0
        self.register_state("name")
        self.register_state("_sys_prompt")

    @property
    def sys_prompt(self) -> str:
        skill = self.toolkit.get_agent_skill_prompt()
        return (self._sys_prompt + "\n\n" + skill) if skill else self._sys_prompt

    async def reply(self, msg: Msg | list[Msg] | None = None) -> Msg:
        """Generate a reply by planning then executing."""
        await self.memory.add(msg)
        plan = await self._plan()
        return await self._execute_plan(plan)

    async def _plan(self) -> ExecutionPlan:
        """Ask the model to generate a structured plan."""
        tools_desc = json.dumps(self.toolkit.get_json_schemas(), ensure_ascii=False) \
            if self.toolkit.tools else "No tools."
        messages = [Msg("system", self.sys_prompt, "system"),
                    *await self.memory.get_memory(),
                    Msg("user", f"Create a step-by-step plan.\nTools:\n{tools_desc}", "user")]
        prompt = await self.formatter.format(messages)
        try:
            res = await self.model(prompt, structured_model=ExecutionPlan)
            if res.metadata and "steps" in res.metadata:
                plan = ExecutionPlan.model_validate(res.metadata)
                if plan.steps:
                    self._plan, self._current_step = plan, 0
                    return plan
        except Exception as e:
            logger.warning("Planning failed for %s: %s", self.name, e)
        # Fallback: single reasoning step
        fallback = ExecutionPlan(goal="Complete request",
            steps=[PlanStep(step_number=1, description="Respond directly")])
        self._plan, self._current_step = fallback, 0
        return fallback

    async def _execute_plan(self, plan: ExecutionPlan) -> Msg:
        """Execute the plan step by step."""
        results: list[str] = []
        for step in plan.steps:
            self._current_step = step.step_number
            if step.tool_name and step.tool_args:
                r = await self._execute_step(step)
            else:
                r = await self._reason_step(step, results)
            results.append(f"Step {step.step_number}: {r}")
        reply_msg = Msg(self.name, "\n".join(results), "assistant")
        await self.memory.add(reply_msg)
        return reply_msg

    async def _execute_step(self, step: PlanStep) -> str:
        """Execute a single tool step."""
        if step.tool_name not in self.toolkit.tools:
            raise ValueError(f"Tool '{step.tool_name}' not in toolkit")
        tc = ToolUseBlock(type="tool_use", id=f"p{step.step_number}",
                          name=step.tool_name, input=step.tool_args or {})
        tool_res = await self.toolkit.call_tool_function(tc)
        text = ""
        async for chunk in tool_res:
            for b in chunk.content:
                if b.get("type") == "text": text += b.get("text", "")
        await self.memory.add(Msg("system", [ToolResultBlock(
            type="tool_result", id=tc["id"], name=step.tool_name, output=text)], "system"))
        return text

    async def _reason_step(self, step: PlanStep, prev: list[str]) -> str:
        """Execute a pure reasoning step."""
        ctx = "\n".join(prev) if prev else "None"
        msgs = [Msg("system", self.sys_prompt, "system"),
                *await self.memory.get_memory(),
                Msg("user", f"{step.description}\nPrevious:\n{ctx}", "user")]
        res = await self.model(await self.formatter.format(msgs))
        text = "".join(b.get("text", "") for b in (res.content or []) if b.get("type") == "text")
        await self.memory.add(Msg("assistant", text, "assistant"))
        return text

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Receive message(s) without replying."""
        await self.memory.add(msg)

    async def handle_interrupt(self, *args: Any, **kwargs: Any) -> Msg:
        """Handle interruption during reply."""
        m = Msg(self.name, f"Interrupted at step {self._current_step}.",
                "assistant", metadata={"_is_interrupted": True})
        await self.print(m, True)
        await self.memory.add(m)
        return m
```

要点：

- `reply` 拆分为 `_plan` + `_execute_plan` 两个阶段（对比 `_react_agent.py` 第 376-537 行的单一循环）
- `_plan` 使用 `structured_model=ExecutionPlan`（对应 `_react_agent.py` 第 567-568 行的模型调用模式）
- `_execute_step` 通过 `Toolkit.call_tool_function` 执行（对应 `_react_agent.py` 第 685 行）
- 构造函数调用 `super().__init__()` 初始化 hook 系统（`_agent_base.py` 第 140-158 行），否则 `_AgentMeta` 包装的 hook 找不到 `_instance_*_hooks`

---

## 3. 第二步：注册并测试

### 3.1 注册

编辑 `src/agentscope/agent/__init__.py`，在第 5 行后添加 `from ._plan_execute_agent import PlanExecuteAgent`，并在 `__all__` 列表中添加。

### 3.2 单元测试

创建 `tests/plan_execute_agent_test.py`。用 mock 隔离模型和 formatter，只测编排逻辑：

```python
# -*- coding: utf-8 -*-
"""Tests for the PlanExecuteAgent."""
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock
from agentscope.agent._plan_execute_agent import PlanExecuteAgent
from agentscope.message import Msg, TextBlock
from agentscope.tool import ToolResponse

def _fmt():
    f = AsyncMock()
    f.format = AsyncMock(side_effect=lambda m: [{"role": x.role, "content": x.get_text_content()} for x in m])
    return f

def _plan(d):
    r = MagicMock(); r.metadata = d; r.content = []; r.id = "p1"; return r

class PlanExecuteAgentTest(IsolatedAsyncioTestCase):
    async def test_reasoning_step(self):
        m = AsyncMock(return_value=_plan({"goal":"g","steps":[
            {"step_number":1,"description":"hello","tool_name":None,"tool_args":None}]}))
        a = PlanExecuteAgent("t","",m,_fmt())
        r = await a(Msg("user","hi","user"))
        self.assertIn("Step 1", r.get_text_content())

    async def test_tool_step(self):
        m = AsyncMock(return_value=_plan({"goal":"g","steps":[
            {"step_number":1,"description":"add","tool_name":"add","tool_args":{"a":1,"b":2}}]}))
        tk = MagicMock(); tk.tools={"add":True}; tk.get_json_schemas.return_value=[{"name":"add"}]
        tk.get_agent_skill_prompt.return_value = ""
        async def mock_call(tc): yield ToolResponse(content=[TextBlock(type="text",text="3")])
        tk.call_tool_function = mock_call
        a = PlanExecuteAgent("t","",m,_fmt(),toolkit=tk)
        r = await a(Msg("user","1+2","user"))
        self.assertIn("add", r.get_text_content())

    async def test_fallback(self):
        m = AsyncMock(return_value=MagicMock(metadata=None,content=[],id="x"))
        r = await PlanExecuteAgent("t","",m,_fmt())(Msg("user","hi","user"))
        self.assertIsNotNone(r)

    async def test_observe(self):
        a = PlanExecuteAgent("t","",AsyncMock(),_fmt())
        await a.observe(Msg("user","x","user"))
        self.assertEqual(len(await a.memory.get_memory()), 1)

    async def test_interrupt(self):
        a = PlanExecuteAgent("t","",AsyncMock(),_fmt())
        r = await a.handle_interrupt()
        self.assertTrue(r.metadata["_is_interrupted"])
```

运行：`pytest tests/plan_execute_agent_test.py -v`

---

## 4. 第三步：进阶功能——计划修订与错误恢复

### 4.1 _replan 方法

在 `__init__` 中添加 `self._replan_count = 0`，然后添加 `_replan` 方法：

```python
    async def _replan(self, failed: PlanStep, error: str,
                      completed: list[str]) -> ExecutionPlan | None:
        """Revise the plan after a step failure."""
        if self._replan_count >= self.max_replans:
            logger.warning("Max replans reached for %s", self.name)
            return None
        prompt_text = (f"Step {failed.step_number} failed: {error}\n"
                       f"Done so far:\n" + "\n".join(completed) + "\nRevise the plan.")
        messages = [Msg("system", self.sys_prompt, "system"),
                    *await self.memory.get_memory(), Msg("user", prompt_text, "user")]
        res = await self.model(await self.formatter.format(messages),
                               structured_model=ExecutionPlan)
        if res.metadata and "steps" in res.metadata:
            self._replan_count += 1
            self._plan = ExecutionPlan.model_validate(res.metadata)
            return self._plan
        return None
```

### 4.2 带恢复的执行循环

替换 `_execute_plan`：

```python
    async def _execute_plan(self, plan: ExecutionPlan) -> Msg:
        """Execute plan with error recovery via replanning."""
        results: list[str] = []
        self._replan_count = 0
        while True:
            ok = True
            for step in plan.steps:
                self._current_step = step.step_number
                try:
                    r = await self._execute_step(step) if step.tool_name and step.tool_args \
                        else await self._reason_step(step, results)
                    results.append(f"Step {step.step_number}: {r}")
                except Exception as e:
                    logger.warning("Step %d failed: %s", step.step_number, e)
                    new_plan = await self._replan(step, str(e), results)
                    if new_plan:
                        plan = new_plan; ok = False; break
                    results.append(f"Step {step.step_number} FAILED: {e}")
            if ok: break
        reply_msg = Msg(self.name, "\n".join(results), "assistant")
        await self.memory.add(reply_msg)
        return reply_msg
```

### 4.3 修订测试

追加到测试类：

```python
    async def test_replan(self):
        m = AsyncMock(side_effect=[
            _plan({"goal":"t","steps":[{"step_number":1,"description":"f",
                     "tool_name":"bad","tool_args":{"x":1}}]}),
            _plan({"goal":"t2","steps":[{"step_number":1,"description":"ok",
                     "tool_name":None,"tool_args":None}]}),
            MagicMock(metadata=None,content=[],id="r1")])
        tk = MagicMock(); tk.tools={"bad":True}; tk.get_json_schemas.return_value=[]
        tk.get_agent_skill_prompt.return_value=""
        async def fail(tc): raise RuntimeError("boom")
        tk.call_tool_function = fail
        r = await PlanExecuteAgent("t","",m,_fmt(),toolkit=tk,max_replans=2)(
            Msg("user","test","user"))
        self.assertIsNotNone(r)
```

---

## 5. 第四步：错误处理

### 5.1 规划阶段

`_plan` 中 `structured_model` 调用可能失败。上面第 2.3 节已用 try/except 保护（`_plan` 方法第 82-90 行），失败时 fallback 到单步计划。两层保护：结构化输出失败 -> 普通文本请求失败 -> 硬编码单步。

### 5.2 执行阶段

`_execute_step` 在第 118 行校验工具名是否存在，抛出 `ValueError` 触发 `_replan`。所有执行异常被第 4.2 节 `_execute_plan` 的 try/except 捕获。

### 5.3 错误处理测试

```python
    async def test_plan_fallback(self):
        m = AsyncMock(side_effect=[RuntimeError("no structured"), MagicMock(metadata=None,content=[],id="fb")])
        r = await PlanExecuteAgent("t","",m,_fmt())(Msg("user","hi","user"))
        self.assertIn("Step 1", r.get_text_content())
```

---

## 6. 第五步：集成测试

需要有效 API key 的端到端验证：

```python
import asyncio
from agentscope.agent import PlanExecuteAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg, TextBlock
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit, ToolResponse

def add_numbers(a: int, b: int) -> ToolResponse:
    """Add two numbers.

    Args:
        a (int): First number.
        b (int): Second number.

    Returns:
        ToolResponse: The sum.
    """
    return ToolResponse(content=[TextBlock(type="text", text=f"Sum is {a+b}")])

def multiply(a: float, b: float) -> ToolResponse:
    """Multiply two numbers.

    Args:
        a (float): First number.
        b (float): Second number.

    Returns:
        ToolResponse: The product.
    """
    return ToolResponse(content=[TextBlock(type="text", text=f"Product is {a*b}")])

async def main():
    model = OpenAIChatModel(model_name="gpt-4o-mini")
    fmt = OpenAIChatFormatter()
    tk = Toolkit()
    tk.register_tool_function(add_numbers)
    tk.register_tool_function(multiply)
    agent = PlanExecuteAgent("calc", "You calculate. Use tools.", model, fmt, toolkit=tk)
    r = await agent(Msg("user", "What is 17+25?", "user"))
    print(r.get_text_content())
    assert len(await agent.memory.get_memory()) >= 2
    r2 = await agent(Msg("user", "Calculate (3+4)*2 step by step.", "user"))
    print(r2.get_text_content())

asyncio.run(main())
```

---

## 7. PR 检查清单

- [ ] 子类 `AgentBase`，实现 `reply`（第 197 行）、`observe`（第 185 行）、`handle_interrupt`（第 516 行）
- [ ] 构造函数调用 `super().__init__()` 初始化 hook 系统（第 140-158 行）
- [ ] `_plan` 使用 `structured_model`，有 try/except fallback
- [ ] `_execute_plan` 支持 `_replan` 错误恢复，受 `max_replans` 限制
- [ ] 工具调用通过 `Toolkit.call_tool_function`（对应 `_react_agent.py` 第 685 行）
- [ ] 所有方法有 Google 风格 docstring
- [ ] 通过 `pytest tests/plan_execute_agent_test.py -v` 和 `pre-commit run --all-files`
- [ ] 在 `agent/__init__.py` 中注册 `PlanExecuteAgent`
- [ ] `_AgentMeta`（`_agent_meta.py` 第 163-174 行）自动包装 hook，无需手动处理

---

## 8. 下一章预告

下一章我们将造一个 RAG 管道——从文档读取、向量化、检索到生成，把知识库接入 AgentScope 的 RAG 模块。
