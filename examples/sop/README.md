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

### Two tests for the boundary

- **Is this a SOP?** If you cannot say how many steps it has and who does
  each without running it — it is not one.
- **Should this be a step?** If nobody actually checks anything at that
  point — it should not be.

### A consequence worth knowing before you design one

**A review is a verifier, not a step.** When a review fails, the work goes
back to whoever produced it — but a failed *step* retries *itself*. Make
"check the storyboard" a step and a failure just re-runs the checker, to
the same conclusion, until the attempt limit ends it.

## The demo

`main.py` turns a story outline into a stylised animation.

```
分镜与建模需求  ------->  Blender 建模与动画  ------->  视频风格化
director                  animator                     colorist
no tools                  Blender over MCP             one ffmpeg tool
|                         |                            |
gate: a person            gate: a person               gate: a person
```

Three steps, three different kinds of work, and a person signs off on
each. A refusal goes back to the right agent every time: a storyboard
that misses a character sends `director` back, a render that skipped a
shot sends `animator` back, a look that fights the story sends `colorist`
back — each with the reason, verbatim.

### Prerequisites

```bash
export DASHSCOPE_API_KEY=sk-...
uv tool install blender-mcp     # step two drives Blender over MCP
# open Blender, enable the blender-mcp addon, start its server
# ffmpeg on PATH                # step three restyles the render

python main.py
python main.py --story "一只猫在雨夜的屋顶上追一片发光的落叶"
```

Say `n` at any approval and give a reason: it goes back to that step's
agent word for word, along with which attempt this is.

### Every verifier here is a person

A verifier is not a special kind of object. It satisfies the same
protocol an agent does, so the step cannot tell a person from a model —
and this one asks and **stops**:

```python
class HumanApproval:
    async def reply_stream(self, inputs=None, structured_schema=None,
                           yield_final_msg=False):
        if not isinstance(inputs, ExternalExecutionResultEvent):
            yield RequireExternalExecutionEvent(...)     # ask, and end
            return
        answer = str(inputs.execution_results[0].output).strip()
        yield AssistantMsg(..., structured_output={"passed": ..., ...})
```

The engine ends the stream rather than holding a coroutine open. The
demo blocks on `input()` with **no agent suspended anywhere behind it**,
and the run picks up when the answer arrives — a second later or a week.
Nothing is remembered on the object: which half of the step it is in is
worked out from the run state, so the same class works after a restart.

### A step hands over an account, not its workspace

Step two reads step one's shot list and modelling list. Step three reads
the path step two rendered to. Nothing else crosses — no files, no
context, no tools:

```
<handover from="Blender 建模与动画">
/tmp/fox/render.mp4 — 6 shots, fox and heron rigged, 42s
</handover>
```

That is the whole contract between steps, and it is what lets each agent
be given exactly the tools its milestone needs and nothing more.

### Driving it

The engine is shaped like an agent, so you feed it and watch:

```python
engine = SOPEngine(sop)
async for event in engine.reply_stream(UserMsg(name="user", content=story)):
    renderer.render(event)
```

Between the agents' own events the engine emits `SOP_STEP_STARTED` and
`SOP_STEP_ENDED`, so a watcher can say "step 2, second attempt, refused"
without reading the run state. When the stream ends, `engine.phase` says
why; `awaiting` means something is waiting on a person — answer whatever
came past and call again.

### A run outlives its process

`engine.state` is plain data, owned by the engine, never by a step:

```python
stored = engine.state.model_dump_json()
...
engine = SOPEngine(sop, SOPRunState.model_validate_json(stored))
```

It covers the SOP's own state and nothing below it — an executor that
keeps state of its own, as an `Agent` does, is persisted by whoever built
it. One definition can drive any number of engines.

### Writing a step that is neither

`SOPStep` is a convenience, not a law. Subclass `SOPStepBase`, implement
`reply_stream(inputs, state)`, and file a verdict with `record()` — one
call is one attempt, and the engine asks no more than that. A step that
must remember more than the engine's fields names a `SOPStepRunState`
subclass in `state_type`.

### What is not here

Everything that needs a service underneath: triggers and schedules,
workspace allocation, notification channels, and persistence. A service
that has them keeps its own records and builds one of these definitions
before running it.
