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

`main.py` turns one line of text into a stylised animation — the shot
everyone knows and nobody has seen: **China lifting the World Cup.**

```
分镜与建模需求  ------->  Blender 建模与动画  ------->  视频风格化
director                  animator                     colorist
frames, not vibes         Blender's own MCP            Wan 2.7 video edit
|                         |                            |
gate: a person            gate: a person               gate: a person
```

### Why Blender in the middle

A video model straight from the text would *guess* at the motion. Blender
does what it is told: the captain's arms rise over exactly 40 frames, the
camera pushes in at one fixed speed, the confetti falls under real
gravity. So the storyboard is written **in frames**, the animator keys
**exactly those frames**, and only then — with the physics settled — does
a video model come in, at the one step where a *look* is what you want.

That is also why the steps split where they do: three different kinds of
work, and a person can check each without knowing the next.

The MCP server is Blender's own (`projects.blender.org/lab/blender_mcp`)
rather than the better-known GitHub one, for three reasons that all
matter to an unattended step: its add-on runs headless with auto-start,
so nobody has to click *Connect*; it ships the bpy API and the manual as
searchable tools, so the animator looks things up instead of guessing;
and it renders straight to a path. It is launched from git, so there is
nothing to clone for the server side:

```python
StdioMCPConfig(
    command="uvx",
    args=["--from", "git+https://projects.blender.org/lab/blender_mcp.git#subdirectory=mcp",
          "blender-mcp"],
)
```

### Prerequisites

```bash
export DASHSCOPE_API_KEY=sk-...
brew install --cask blender
# install Blender's own MCP add-on — addon/blender_mcp_addon from
# https://projects.blender.org/lab/blender_mcp — and enable auto-start
# in its preferences. The MCP server is fetched from that repo by uvx.

python main.py
python main.py --story "马里奥跳起顶碎砖块，金币弹出的那一下"
```

Say `n` at any approval and give a reason: it goes back to that step's
agent word for word, along with which attempt this is.

### The agents share one workspace

All three are built on the same `LocalWorkspace`, so the render the
animator writes is right there for the colorist:

```python
    shared = await workspace.list_tools()
    animator = Agent(..., toolkit=Toolkit(tools=[*shared, *await blender.list_tools()]),
                     offloader=workspace)
    colorist = Agent(..., toolkit=Toolkit(tools=[*shared, FunctionTool(restyle_video)]),
                     offloader=workspace)
```

What crosses between steps is still an **account**, not the workspace:
the shot list, then the render's path and a line per shot, then the
result's path and the look chosen. Each agent is handed exactly the tools
its milestone needs on top of that.

### The restyle is one tool

`restyle_video` calls Wan 2.7's video-editing model through the DashScope
SDK. A local path is enough — the SDK uploads it and resolves the
temporary URL itself — and the result is written back beside the source:

```python
        task = VideoSynthesis.async_call(
            model="wan2.7-videoedit",
            media=[{"type": "video", "url": video_path}],
            prompt=look,
            resolution="720P",
            api_key=api_key,
        )
        done = VideoSynthesis.wait(task, api_key=api_key)
```

The input has to be 2–10 seconds, which is why the storyboard is told to
stay under ten.

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
