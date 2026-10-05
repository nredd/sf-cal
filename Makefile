.PHONY: help install format lint type test tox check build clean all

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies with uv and the prek git hooks
	uv sync --all-groups
	uv run prek install

format:  ## Format code with ruff
	uv run ruff format .

lint:  ## Lint code with ruff
	uv run ruff check . --fix --ignore-noqa

type:  ## Type check with ty
	uv run ty check

test:  ## Run tests with pytest
	uv run pytest -v --cov --cov-report=term-missing

tox:  ## Run matrix tests
	uv run tox

check:  ## Non-mutating gate for CI: format check, lint, types, tests
	uv run ruff format --check .
	uv run ruff check . --ignore-noqa
	uv run ty check
	uv run pytest -q --cov --cov-report=term-missing

build:  ## Build every feed from live sources into site/ (local preview)
	uv run sfcal build --cfg sfcal.toml --state site/events.json --out site/

clean:  ## Clean artifacts
	rm -rf .pytest_cache .ruff_cache .tox .coverage dist build *.egg-info site
	find . -type d -name __pycache__ -exec rm -rf {} +

all: format lint type test  ## Run full CI pipeline
