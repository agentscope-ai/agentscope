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
gate: a model         gate: a supervisor     gate: a model checks the
holds the records     signs off              draft against the approved
and audits the                               offer
account
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

### A gate that checks against the same records

The first step is judged by a model that is handed `orders.json` and
`shipments.json` itself:

```python
class FactsMatchRecords(VerifierBase):
    def __init__(self, model, workspace, files):
        self._model = model
        self._backend = workspace.get_backend()
        self._files = [
            self._backend.join_path(workspace.workdir, "data", name)
            for name in files
        ]
```

That is the difference between "the facts were checked" and "the write-up
read plausibly". A judge without the records can only tell you the second.

Note what it takes at construction — a model *and* a workspace. **Whatever
a verifier needs, it takes when it is built.** The engine never learns
what a workspace is, which is what keeps it runnable with no service
underneath.

(Resolve paths against `workspace.workdir`, not the backend's working
directory. The latter is wherever the process happens to be, and is a
reliable way to read nothing at all.)

### A gate that waits without waiting

```python
class SupervisorApproval(VerifierBase):
    def __init__(self):
        self.answer = None

    async def verify(self, sop, run, step, step_run):
        answer, self.answer = self.answer, None
        return answer          # None = no answer yet
```

Answering `None` parks the step. The engine ends the stream rather than
holding a coroutine open, the demo blocks on `input()` with **no agent
suspended anywhere behind it**, and the run picks up when a verdict
arrives — a second later or a week.

Because it is a live object it can remember whether it has already asked —
**at this layer only**. An object does not survive the process, so a
verifier that has to outlive one keeps that in its own storage.

### Why the writer has no tools

Whoever writes to the customer works from the **approved offer**, not from
the order system. They should not be able to look up the order again,
re-interpret the policy, or quietly improve on what the supervisor signed.

The first two steps share the workspace — different agents, same read-only
truth, which is perfectly normal. **The text-only contract matters at the
boundary that matters**, and this is that boundary.

### Driving it

The engine is shaped like an agent: it holds its run the way an agent
holds its state, and you feed it.

```python
engine = SOPEngine(sop)
async for event in engine.run_stream([TextBlock(text=complaint)]):
    show(event)
```

A run stops for two different reasons, and the demo tells them apart by
what came out of the stream rather than by asking around:

- an **agent stopped for permission** — a `RequireUserConfirmEvent` came
  past, so answer it with a `UserConfirmResultEvent`. The answer names the
  *reply*, not the agent; the engine hands it to whichever step's agent was
  waiting on it, so you never have to work out who asked.
- a **verifier is waiting** — nothing came past, and a step sits in
  `verifying`.

Either way `engine.run` says where it stopped, and calling again carries
on. It is plain data: dump it and the progress outlives the process; hand
it back with `SOPEngine(sop, run=stored)` and the run resumes.

### What is not here

Everything that needs a service underneath: triggers and schedules,
workspace allocation, notification channels, agent-to-agent messaging, and
persistence. There is no scheduler, workspace manager, channel or message
bus at this layer. A service that has them keeps its own records and builds
one of these definitions before running it — the way `AgentData` becomes a
live `Agent` today.
