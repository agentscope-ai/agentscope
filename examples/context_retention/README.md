# Explicit context retention

Run from the repository root after installing the package:

```bash
pip install -e .
python examples/context_retention/main.py
```

The demo needs no API key or network connection. A local `ChatModelBase`
subclass returns a fixed structured summary through the normal Agent
compression path. It verifies retention and state updates, not model quality.

The script:

1. Constructs `Agent(context_retention_policy=PinnedContextRetentionPolicy(
   max_pinned_tokens=64))`.
2. Marks an entire message with
   `metadata={"context_retention": True}`.
3. Triggers two compressions and checks that the exact message survives,
   alongside the most recent context that fits.
4. Adds an oversized pinned message, catches `ValueError`, and verifies that
   the complete state and previous summary remain unchanged.

The terminal prints the retained message IDs for both rounds and confirms
that overflow was rejected before the next summary generation.

## Budget and protocol behavior

`max_pinned_tokens` caps the incremental estimated cost of marked messages
and their tool-call/result counterparts. The fixed system prompt, existing
summary, and selected tool schemas do not consume this independent pin cap.
They do count toward the retained-input target derived from
`ContextConfig.reserve_ratio`. Nonempty pins that cannot fit either limit
raise `ValueError`; they never silently fall back to summarization.

Unfinished tool calls remain visible even when they exceed the soft retained
target. Completed tool calls and results remain together. Model input is
checked again after producing a new summary because the summary can grow.
Omitting `context_retention_policy` preserves the existing compression path.

Message metadata uses existing `AgentState` serialization. Recreate the policy
instance when reconstructing an Agent; it is a runtime dependency, not part
of the serialized `ContextConfig`.

The first version pins whole messages. A single Agent reply can contain many
reasoning and tool blocks, so pinning such a message can consume a substantial
budget. Retention does not restore data already truncated from tool results
or removed by the existing image limit. Unmark a message explicitly when its
verbatim content is no longer needed.
