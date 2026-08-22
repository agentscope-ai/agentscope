# Standard Operating Procedures

A **SOP** is a fixed, reusable procedure where every step has to prove it
is done before the next one starts. It fixes the skeleton and leaves the
flesh to the agents: you author the order of the steps and what each must
prove, while *how* any one step gets done is left entirely to the agent
that runs it.

`main.py` runs a three-step one — outline a note, write it to disk,
announce it — with a person signing off at the end.

## What this demo is really about

**The handover is text.** Steps one and two share the same agent, so they
share its context and its files. Step three is a *different* agent with no
tools at all: the only thing it can see is what step two **submitted**.
That is the whole contract between steps — prose, never a workspace.

**Waiting costs nothing.** The last step is accepted by a person, and its
verifier says so by answering *nothing at all*. The engine ends the stream
rather than holding a coroutine open, `main.py` then blocks on `input()`
with no agent suspended anywhere behind it, and the run picks up when an
answer arrives. That is the same shape as an agent parking on a
confirmation, and it is why a run can wait a week without occupying
anything.

## Quickstart

```bash
export DASHSCOPE_API_KEY=sk-...
python main.py
python main.py --topic "How our release process works"
```

Say `n` at the sign-off and give a reason: the reason goes back to the
agent verbatim and it tries again, up to the step's `max_attempts`.

## How a SOP is written

At this layer a SOP is **code**. A step holds the agent that runs it and
the verifier that judges it, both already built — there is no id to
resolve and no spec to materialise:

```python
SOPStep(
    subject="Write the note",
    description="Write the note to note.md, following the outline.",
    agent=writer,                        # a live Agent
    blocked_by=["outline"],              # order comes from here, not the list
    verifier=FileWritten(workspace, "note.md"),
)
```

Reuse one agent across steps and they share its context and workspace;
give each its own and they do not. Run the same SOP twice with the same
agent and its state carries over; build a fresh one per run and nothing
does. All of that is decided by how you construct — there is no mode to
declare.

## Writing a verifier

Subclass `VerifierBase` and answer one of three ways: a passing record, a
failing one with a reason, or `None` for "no answer yet".

```python
class FileWritten(VerifierBase):
    def __init__(self, workspace, path):
        self._backend = workspace.get_backend()
        self._path = self._backend.join_path(workspace.workdir, path)

    async def verify(self, sop, run, step, step_run):
        found = await self._backend.file_exists(self._path)
        return VerificationRecord(
            passed=found,
            message="" if found else f"{self._path} is not there yet.",
        )
```

Two rules worth knowing:

- **Whatever it needs, it takes at construction** — a model, an HTTP
  client, a workspace. The engine never learns what a workspace is, which
  is what keeps it runnable without a service underneath.
- **Say what is missing, not that something is.** The `message` goes back
  to the agent verbatim on the retry, so it is the whole of what the agent
  has to work with.

A verifier needing a person returns `None` until it has an answer:

```python
class HumanApproval(VerifierBase):
    def __init__(self):
        self.answer = None

    async def verify(self, sop, run, step, step_run):
        answer, self.answer = self.answer, None
        return answer
```

Because it is a live object it can remember whether it has already asked —
**at this layer only**. An object does not survive the process, so a
verifier that has to outlive one keeps that in its own storage.

## Driving it

The engine is shaped like an agent: it holds its run the way an agent
holds its state, and you feed it.

```python
engine = SOPEngine(sop)
inputs = [TextBlock(text=topic)]

while True:
    async for event in engine.run_stream(inputs):
        show(event)
    inputs = None

    if engine.status is not SOPRunStatus.RUNNING:
        break

    # The stream ended without settling, so something is waiting.
    approval.answer = ask_the_human(engine)
```

The stream ends when the run settles **or** when a whole pass moved
nothing. Both look the same from out here: `engine.run` says where it
stopped, and calling again carries on.

`engine.run` is plain data. Dump it and the progress outlives the process;
hand it back with `SOPEngine(sop, run=stored)` and the run resumes.

## What you will see

```
── Outline the note · running
[the writer works]
── Outline the note · verifying
── Outline the note · completed
── Write the note · running
[the writer writes note.md]
── Write the note · completed
── Announce it · running
[the editor writes an announcement, from the text alone]
── Announce it · verifying

────────────────────────────────────────────────────────────
Announce it is waiting for you:

We shipped SOP support...
────────────────────────────────────────────────────────────
Accept it? [y/N]
```

`StepStateEvent` is what makes that stream readable — without it the agent
events arrive unlabelled and you cannot tell whose they are.

## What is not here

Everything needing a service underneath: triggers and schedules,
workspace allocation, notification channels, agent-to-agent messaging, and
persistence. There is no scheduler, workspace manager, channel or message
bus at this layer. A service that has them keeps its own records and
builds one of these definitions before running it — the way `AgentData`
becomes a live `Agent` today.
