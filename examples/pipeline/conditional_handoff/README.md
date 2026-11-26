# Conditional handoff between agents

Son wants a $30 basketball. He asks Mother for it. If her budget covers
the price, she answers him directly. If she needs Father's help, Father
hears both the request and her answer before arranging a shopping trip
with Son. Daughter hears the final plan through `Agent.observe()`.

## Run

From the repository root, install AgentScope with `python -m pip install -e .`
and set `DASHSCOPE_API_KEY` in your environment. Run both budgets to see
the speaker order change.

| Command | Speakers in order |
| --- | --- |
| `python examples/pipeline/conditional_handoff/main.py --mother-budget 10` | Son → Mother → Father → Son |
| `python examples/pipeline/conditional_handoff/main.py --mother-budget 30` | Son → Mother → Son |

## How the handoff works

Each agent keeps its own conversation history. The `speak()` helper calls
`Agent.reply()` and wraps the speaker's text in a named `UserMsg` for the
recipient. `DashScopeMultiAgentFormatter` includes the speaker names in
the model input. The budget check in Python decides who speaks next.
Calling `observe()` adds the plan to Daughter's history without asking
the model to generate another turn.
