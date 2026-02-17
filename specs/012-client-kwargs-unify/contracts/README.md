# Contracts: 012-client-kwargs-unify

No new external API contracts are introduced.

Behavioral notes:
- DashScope constructor tolerates additional kwargs.
- Ollama supports `client_kwargs` (client init) and `generate_kwargs` (default
  generation kwargs).
