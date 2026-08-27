# Goal Pipeline

This example demonstrates `agentscope.pipeline.GoalPipeline`: run one
agent until a second agent agrees the goal has been reached.

## What the demo shows

`goal_pipeline.py` builds two agents over a shared `LocalWorkspace` and
loops them:

- **Executor** — writes the code, using the workspace's filesystem
  tools.
- **Verifier** — an ordinary `Agent`, not a special kind of object. Its
  verdict comes from structured output (`passed` plus a `message`
  explaining what is missing), so a check that has to read files or run
  commands does it with the same tools the executor has.
- **The loop** — a refusal goes back to the executor verbatim as
  feedback and it tries again, up to `max_iters` times.

Because both agents share one workspace, the verifier judges what was
actually written rather than what the executor claims it wrote.

## Quickstart

```bash
export DASHSCOPE_API_KEY=sk-...
python goal_pipeline.py
```

The pipeline is handed straight to `launch_console`, so the terminal
shows both agents' streams as they take turns.

## Resuming

`reply_stream` ends when a tool call needs human confirmation — nothing
is left suspended waiting. Feed the answer back in to carry on:

```python
async for event in pipe.reply_stream(user_confirm_result_event):
    ...
```

The event's `reply_id` says which of the two agents was parked, so the
caller does not have to track whose turn it was. The iteration budget
survives the round trip: resuming does not hand the run a fresh set of
attempts.
