.PHONY: setup lint format format-check test check skills-check

setup:
	uv sync
	uv run pre-commit install

lint:
	uv run ruff check .

format:
	uv run ruff format . && uv run ruff check --fix .

format-check:
	uv run ruff format --check .

test:
	uv run pytest

skills-check:
	uv run python scripts/sync_agent_skills.py --check

check: lint format-check test skills-check
