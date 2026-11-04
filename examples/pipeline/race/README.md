# First accepted reply from independent agents

`RacePipeline` runs independent agents on a safely repeatable task, returning a
completed reply only when an application predicate accepts it. A fast provider
failure or a rejected answer does not defeat another participant's valid result.
The acceptance predicate is an application check, not an evaluator of factual
correctness supplied by AgentScope.

```python
from agentscope.agent import Agent
from agentscope.message import Msg, UserMsg
from agentscope.pipeline import RacePipeline


async def retrieve(agents: list[Agent]) -> Msg:
    # Configure separate model/state objects and read-only retrieval tools on
    # the agents before calling this function. Do not share an active agent
    # with another pipeline or concurrently call it directly.
    race = RacePipeline(
        agents=agents,
        max_concurrency=2,
        accept=lambda reply: bool(reply.get_text_content().strip()),
    )
    return await race.reply(UserMsg("user", "Find the current release notes."))
```

Each participant receives an input copy. Its context remains its own and is
retained after the race. The returned message preserves the winning agent's name
and reply ID. When several candidates finish in the same scheduler batch, input
order breaks the tie. All-participant failure raises an `ExceptionGroup` with
exceptions ordered by participant; errors in the acceptance predicate propagate
directly after cleanup.

The concurrency limit bounds active replies, not total tokens, cost or external
side effects. Queued participants may start before a finished result is accepted.
Cancelling a tool cannot undo an operation it has already performed. Use this
pattern for safely repeatable retrieval/analysis, not competing writes or payments.

Before returning, the race cancels and awaits remaining tasks. A caller timeout or
cancellation also waits for cooperative cleanup. Tools that ignore cancellation or
never finish cleanup can delay return; there is no forced task termination.

This first version does not expose speculative participant events:
`reply_stream()` yields exactly the accepted final message, after cleanup, rather
than incremental text. It is intended for direct Python use, not as a drop-in
streaming console/service agent. HITL continuation and external-execution handoff
are unsupported. A participant that parks is interrupted through `Agent.reply`
and counted as a failed candidate; a control event cannot resume the race. No
automatic retry, shared checkpoint, or cross-process recovery is provided.

The offline regression suite exercises real Agent/model/tool boundaries without
provider credentials:

```sh
pytest tests/pipeline_race_test.py
```

This is a proposed orchestration capability under #2479; its placement and public
contract remain subject to maintainer review. No production latency, cost or answer
quality improvement has been measured.
