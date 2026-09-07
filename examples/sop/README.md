# Standard Operating Procedures

## What a SOP is

**A fixed skeleton with free flesh.**

The **skeleton** — which milestones there are, in what order, and what
each must prove — is written by a person and can be read without running
anything. The **flesh** — how any of it actually gets done — belongs to
the agents, and the SOP neither sees it nor prescribes it.

In one line: *a fixed sequence of milestones, each of which must be
verified before the next can start; how a milestone is reached is nobody
else's business.*

Three things follow, and they explain every decision in the design.

**A step is a checkpoint somebody cares about, not a unit of work.**
"Establish what happened" is one step even if it means reading four
systems, because what a person checks at that point is whether the story
is straight — not whether the third lookup is done.

**Verification is the whole point, not a feature.** Without it a SOP is
just an ordered list. Because an agent's output is uncertain, "claims to
be done" and "is done" have to be different things — which is exactly
what a workflow engine does not need, since its nodes are code.

**Being fixed is where the value comes from.** Auditability, reuse and
predictability all follow from it.

### Two tests for the boundary

- **Is this a SOP?** If you cannot say how many steps it has and who does
  each without running it — it is not one.
- **Should this be a step?** If nobody actually checks anything at that
  point — it should not be.

The first rules out runtime fan-out, computed branching and steps that
appear as you go. The second rules out lifting an agent's own breakdown
of its work into the procedure.

### What it deliberately is not

| Not | Because |
| --- | --- |
| a workflow graph | allow computed branching and runtime fan-out and you have rebuilt LangGraph; that is a border we choose not to cross |
| a task tracker | how an agent splits its own work is its business |
| a plan | plans change; a SOP does not. The changing part is what agents do inside a step |

### Where things live

| You want | It lives |
| --- | --- |
| loops, retries | inside a step, and in the retry a refused verification starts |
| splitting into N parts, handing them out | inside a step — the agent decomposes, and may spawn its own sub-agents |
| branching ("if X, do Y instead") | inside a step |
| who executes | on the SOP, fixed |
| what counts as done | on the SOP, fixed |
| what order | on the SOP, fixed |

The right-hand three are authored by a person. The left-hand three are the
agent's freedom. **That line is all a SOP is.**

### A consequence worth knowing before you design one

**A review is a verifier, not a step.**

When a review fails, the work goes back to whoever produced it — but a
failed *step* retries *itself*. Make "fact-check" a step and a failure
just re-runs the fact-checker, to the same conclusion, until the attempt
limit ends it.

```
wrong   1. write   2. fact-check (a step)   fails -> check again -> same
right   1. write, verified by a fact-check  fails -> back to the writer,
                                                     with the specifics
```

## The demo

`main.py` handles a customer complaint by the book.

```
核实事实  --------->  拟补偿方案  --------->  写回复客户
support               policy                 writer
reads the records     reads the policy       no tools at all
|                     |                      |
gate: an auditor      gate: a supervisor     gate: a safety agent
agent reads the       signs off              checks the draft against
records itself                               the approved offer
```

Three steps, three different kinds of work, three gates of different
natures — and none of them swallows the others. A refusal goes back to the
right agent every time: facts that do not hold up send `support` back to
look again, an offer the supervisor rejects sends `policy` back to
rethink, a letter that over-promises sends `writer` back to redraft.

### Quickstart

```bash
export DASHSCOPE_API_KEY=sk-...
python main.py
python main.py --complaint "订单 A-1051 到现在还没动静"
```

Say `n` at the approval and give a reason: it goes back to the policy
agent verbatim, and it tries again.

### Where the facts come from

`data/` stands in for the systems a support agent would really query:

```
data/orders.json      what was ordered, and what was promised
data/shipments.json   where the parcel actually got to
data/policy.md        what may be offered, and what needs a supervisor
```

Real support agents would reach an order service, a courier API and a
policy wiki instead — most likely over MCP. **The SOP neither knows nor
cares.** All it says is that step one must hand over an account that
survives checking.

### The engine sees verdicts, never verifiers

A verifier is not a special kind of object. It satisfies the same
protocol an agent does, and the step drives it the same way — so the
first gate is simply an agent that can read the records itself:

```python
auditor = Agent(
    name="auditor",
    system_prompt=(
        f"You audit a support agent's account of what happened. The "
        f"records in {data} are the only source of truth — read them "
        "and check every factual claim against them."
    ),
    model=model(),
    toolkit=Toolkit(tools=tools),
    offloader=workspace,
)
```

That is the difference between "the facts were checked" and "the write-up
read plausibly". A judge without the records can only tell you the second.

The engine never learns what a workspace is, or a model, or an auditor.
It reads `state.verifications` and decides from that alone: pass, retry,
or — once `max_attempts` is spent — fail.

### A gate that waits without waiting

The supervisor is a person, so the second gate is a small object rather
than an agent. Same protocol, so the step cannot tell:

```python
class SupervisorApproval:
    async def reply_stream(self, inputs=None, structured_schema=None,
                           yield_final_msg=False):
        if not isinstance(inputs, ExternalExecutionResultEvent):
            yield RequireExternalExecutionEvent(...)     # ask, and stop
            return
        answer = str(inputs.execution_results[0].output).strip()
        yield AssistantMsg(..., structured_output={"passed": ..., ...})
```

Asked with nothing to go on it posts its question and ends. **The engine
ends the stream rather than holding a coroutine open**, the demo blocks
on `input()` with no agent suspended anywhere behind it, and the run
picks up when the answer arrives — a second later or a week.

Asked again with an answer, it turns that answer into a verdict. Nothing
is remembered on the object: which half of the step it is in is worked
out from the run state, so the same class works after a restart.

### A refusal is a critique

Whatever a verifier says on the way to `passed=False` is handed to the
executor verbatim on its next attempt, together with which attempt it is:

```
<system-reminder>You are running one step of the SOP.

## 拟补偿方案
...

Your last attempt was not accepted:
补偿金额超出 3.2 条上限，应为 40 元券而非全额退款。
This is attempt 2 of 3.</system-reminder>
```

Say `n` at the approval and give a reason: that reason is what the policy
agent reads next.

### Why the writer has no tools

Whoever writes to the customer works from the **approved offer**, not from
the order system. They should not be able to look up the order again,
re-interpret the policy, or quietly improve on what the supervisor signed.

The first two steps share the workspace — different agents, same read-only
truth, which is perfectly normal. **The text-only contract matters at the
boundary that matters**, and this is that boundary.

### Driving it

The engine is shaped like an agent, so you feed it and watch:

```python
engine = SOPEngine(sop)
async for event in engine.reply_stream(UserMsg(name="user", content=complaint)):
    renderer.render(event)
```

When it stops, `engine.status` says why. `awaiting` means something is
waiting on a person; answer whatever came past — a
`UserConfirmResultEvent` for a tool-call confirmation, an
`ExternalExecutionResultEvent` for a question — and call again.

### A run outlives its process

`engine.state` is plain data, gathered from the steps:

```python
stored = engine.state.model_dump_json()
...
engine = SOPEngine(sop, SOPRunState.model_validate_json(stored))
```

It covers the SOP's own state and nothing below it. An executor that
keeps state of its own — an `Agent` does — is persisted by whoever built
it, the same way it is built. Loading a run that belongs to a different
SOP is a `ValueError`, not a surprise.

### Writing a step that is neither

`SOPStep` is a convenience, not a law. Subclass `SOPStepBase`, implement
`reply_stream`, and file a verdict with `record()` — one call is one
attempt, and the engine asks no more than that.

```python
class WaitForCI(SOPStepBase):
    async def reply_stream(self, inputs=None):
        run = await self._client.latest(self.state.submission)
        if run is None:
            return                          # nothing to say yet
        self.record(run.passed, run.summary, "ci")
```

### What is not here

Everything that needs a service underneath: triggers and schedules,
workspace allocation, notification channels, agent-to-agent messaging, and
persistence. There is no scheduler, workspace manager, channel or message
bus at this layer. A service that has them keeps its own records and builds
one of these definitions before running it — the way `AgentData` becomes a
live `Agent` today.
