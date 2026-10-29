# Team Pipeline

This example demonstrates `agentscope.pipeline.TeamPipeline`: a leader delegates independent tasks to two members and combines their results.

## What the demo shows

`team_pipeline.py` builds a planning team:

- **Planner** — proposes an actionable plan from the user's requirements.
- **Reviewer** — independently identifies constraints, risks, and recommendations.
- **Leader** — asks both members in the same round, then combines their findings into the final plan.

Assignments to different members in the same round run concurrently. The prompt asks the leader to issue both `TeamAssign` calls together; actual tool-call decisions depend on the model. Members have their own conversations, so each assignment includes the necessary context.

## Quickstart

```bash
export DASHSCOPE_API_KEY=sk-...
python team_pipeline.py
```

The pipeline is handed straight to `launch_console`, so the terminal shows the leader's and members' streams.
