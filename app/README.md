# Relation-Zettel App Workspace

This directory holds the implementation work for the Relation-Zettel顿悟工具.
The structure follows the architecture + design docs:

- `backend/` — FastAPI service skeleton with Canvas/Relation endpoints.
- `frontend/` — React + Vite minimalist UI implementing Canvas → Connection Card.

Use poetry (backend) and npm (frontend) to install dependencies. Both services are
currently wired to mock data so the experience can be validated before the
Relation Factory and persistence layers are completed.
