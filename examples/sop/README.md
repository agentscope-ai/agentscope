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

`main.py` turns one line of text into a stylised animation — say **a ship
crossing a planet, alone between the stars.**

```
分镜与建模需求  ------->  Blender 白膜动画  ------->  风格化上色
director                  blocker                    colorist
frames, not vibes         grey geometry, exact       Wan 3.0 paints it
                          motion, right camera
|                         |                          |
gate: a person            gate: a person             gate: a person
```

### Why Blender in the middle

A video model straight from the text would *guess* at the motion. Blender
does what it is told: the ship drifts across frame at one fixed speed, the
camera pushes in over exactly 40 frames, the planet keeps its scale
against the hull.

So the middle step renders a **white model** — grey untextured geometry,
right shapes, right motion, right camera, no materials and no lighting
design — and the last step hands that to Wan 3.0 to paint. Physics from
the one that can be told; looks from the one that is good at looks. The
storyboard is written **in frames** and the blocker keys **exactly those
frames**, so nothing downstream can drift.

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

# Once: Blender 5.1+ plus its MCP add-on, which is not on
# extensions.blender.org and so has to be built from source.
python setup_blender.py
python setup_blender.py --blender /path/to/blender   # already have one

python main.py
```

Describe a scene in the composer to start, e.g. `马里奥跳起顶碎砖块，金币
弹出的那一下`.

`main.py` starts Blender headless and shuts it down on the way out, so
nothing has to be running first. Leave a Blender open with the add-on
connected and that one is used instead, and you can watch the scene
being built.

### Approvals happen in the terminal UI

The whole run is driven by `launch_tui(engine)` — the SOP engine is a
`PipelineProtocol`, so the TUI drives it exactly as it drives an agent.
When a step needs signing off, the verifier asks through the standard
`AskUser` tool and the TUI draws the form; picking **Send back** and
typing a reason puts that reason in front of the step's agent word for
word, along with which attempt this is.

Nothing is held open behind that form. The verifier posts its question
and the stream ends; the answer starts the run again from its state — a
second later or a week.

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

### Painting the white model is one tool

`restyle_video` hands the render to `wan3.0-video` as a **reference
video**, so the motion, timing and framing that come back are the ones
Blender produced — only the look changes.

A reference video cannot be inlined the way an image can, so the render
goes to Model Studio's temporary space first and comes back as an
`oss://` URL good for 48 hours:

```python
        task = VideoSynthesis.async_call(
            api_key=api_key,
            model=VIDEO_MODEL,
            prompt=look,
            media=[{"type": "reference_video",
                    "url": _upload(video_path, api_key)}],
            resolution="720P",
            ratio="adaptive",
            duration=-1,
            prompt_extend=True,
        )
        done = VideoSynthesis.wait(task=task, api_key=api_key)
```

The upload is a `getPolicy` call followed by a form POST — the SDK adds
`X-DashScope-OssResourceResolve` itself, and binds the file to the model
that will read it. The model's own limits on a reference video are why
the storyboard is told to stay under **15 seconds** at **≥24 fps**.

`duration=-1` is what keeps the styled cut as long as the render — the
parameter defaults to 5 seconds, which would quietly truncate anything
longer.

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
