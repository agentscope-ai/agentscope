# Relation-Zettel Backend

FastAPI service scaffold aligned with the architecture design. It now includes:

- Async SQLAlchemy models for relations, evidence, audit, blacklist, experience metrics.
- Canvas submission endpoint wired to a real AgentScope ReActAgent pipeline (with a fallback stub) that returns
  structured `claim/reason/evidence` and persists them.
- Suggest / decide / swap flows that read + update the database with undo support.
- Review, audit, and metrics endpoints returning structured JSON.
- Centralised configuration (`app/config.py`) loading `.env` for database + API keys.
- AI registry (`app/ai_registry.py`) and prompt file (`app/prompts/relation_factory.md`) to tweak model/prompt per function.
- Config endpoints to inspect & update env at runtime: `GET /config/info`, `POST /config/set`.

## Run locally

```bash
# install deps
poetry install

# copy env and set your keys
cp app/backend/.env.example app/backend/.env
# edit app/backend/.env
#   - DATABASE_URL=sqlite+aiosqlite:///./relation_zettel.db
#   - LLM_PROVIDER=openai|dashscope|anthropic|gemini|ollama|glm
#   - LLM_API_KEY=sk-...
#   - RELATION_FACTORY_MODEL=gpt-4o-mini (or glm-4-flash, etc.)
#   - RELATION_FACTORY_PROMPT_PATH=app/backend/app/prompts/relation_factory.md
#   - GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4

# run dev server
poetry run uvicorn app.main:app --reload
```

### Using GLM (ZhipuAI)
- Set `LLM_PROVIDER=glm`, `LLM_API_KEY=<your_zhipu_api_key>`, and optionally `GLM_BASE_URL` (defaults to
  `https://open.bigmodel.cn/api/paas/v4`).
- We use the OpenAI python client with `base_url` pointing to ZhipuAI's OpenAI‑compatible API, so the AgentScope
  `OpenAIChatModel` works seamlessly.

### Endpoints
- `POST /canvas/submit` — runs Relation Factory (AgentScope) and returns the first candidate.
- `GET /relations/suggest` — fetch latest proposed for a subject.
- `POST /relations/decide` — verify/reject/undo with 3s undo window.
- `POST /relations/swap` — swap to next best proposed candidate (one at a time).
- `GET /review/daily` — daily review cards (sequential, one at a time in UI).
- `GET /audit/:id` — audit overview, artifacts (placeholder paths for now).
- `GET /metrics` — Prometheus metrics (placeholder values for now).
- `GET /config/info` — shows provider/model/prompt path to verify configuration.
- `POST /config/set` — write `.env` keys (whitelist: DATABASE_URL, LLM_PROVIDER, LLM_API_KEY, RELATION_FACTORY_MODEL,
  RELATION_FACTORY_PROMPT_PATH, BUDGET_CENTS_DEFAULT, GLM_BASE_URL).

### Tweak AI prompts/models
- Edit `app/backend/app/prompts/relation_factory.md`.
- Override provider/model in `.env`.

Notes:
- If you are inside this repository without `pip install agentscope`, the backend auto‑loads Agentscope from `src/`.
- Network/API calls depend on provider availability and keys. In offline/no‑key environments we fallback to deterministic stubs.
