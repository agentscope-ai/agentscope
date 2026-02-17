# Research: 012-client-kwargs-unify

## Decision: Keep `client_args` contract for OpenAI/Gemini/Anthropic

- **Decision**: Do not rename `client_args` to `client_kwargs` for these models
  in this batch.
- **Rationale**: `docs/model/SOP.md`, tutorials, and tests define `client_args`
  as the contract. Renaming would be a broader change.

## Decision: Add `client_kwargs`/`generate_kwargs` to Ollama

- **Decision**: Split client initialization kwargs from generation kwargs.
- **Rationale**: Matches upstream intent and improves consistency.
