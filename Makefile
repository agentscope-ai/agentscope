.PHONY: backend frontend dev-backend dev-frontend lint-backend test-backend

backend:
	cd app/backend && poetry run uvicorn app.main:app --reload

frontend:
	cd app/frontend && npm run dev

dev-backend:
	cd app/backend && poetry install

lint-backend:
	cd app/backend && poetry run ruff app

test-backend:
	cd app/backend && poetry run pytest
