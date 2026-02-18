# Research Notes

## Decision 1: Compatibility strategy
- **Decision**: Perform a hard cut to `client_kwargs` for OpenAI/Gemini/Anthropic constructors.
- **Rationale**: User approved direct switch to reduce long-term naming divergence.
- **Alternatives considered**: Keep deprecated alias `client_args` with warnings.

## Decision 2: Scope control
- **Decision**: Include all visible docs/examples/tests that instantiate these models.
- **Rationale**: Prevent immediate breakage from stale copy-paste examples.
- **Alternatives considered**: Update only files touched by upstream commit.
