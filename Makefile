.PHONY: setup lint format format-check test check skills-check eval classifier

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

eval:
	uv run python -m evals.evaluate $(if $(wildcard evals/data/real_prs.jsonl),--real-prs evals/data/real_prs.jsonl --real-prs-registry evals/data/real_prs_registry.jsonl.gz)

classifier:
	uv run python -m evals.classifier

check: lint format-check test skills-check
