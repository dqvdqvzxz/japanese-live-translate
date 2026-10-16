SHELL := /bin/sh
BACKEND := packages/backend
CAPTURE := packages/audio-capture
DESKTOP := packages/desktop

.PHONY: help setup test lint capture-build capture-selftest verify-openai clean doctor

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

doctor: ## Report which required toolchains are present
	@printf 'python3  : '; python3 --version 2>&1 || echo missing
	@printf 'uv       : '; uv --version 2>&1 || echo 'missing (needed for Python 3.12)'
	@printf 'swift    : '; swift --version 2>&1 | head -1 || echo missing
	@printf 'node     : '; node --version 2>&1 || echo missing
	@printf 'cargo    : '; cargo --version 2>&1 || echo 'missing (needed for Tauri)'

setup: ## Install dependencies for every package
	cd $(BACKEND) && uv sync --extra dev
	cd $(DESKTOP) && npm install
	$(MAKE) capture-build

capture-build: ## Build the Swift audio helper
	cd $(CAPTURE) && swift build -c release

capture-selftest: ## Record 10s from a source and write a WAV to listen to
	./scripts/capture_selftest.sh $(SOURCE)

test: ## Run backend and frontend tests
	cd $(BACKEND) && uv run pytest -q
	cd $(DESKTOP) && npm test --if-present

lint: ## Lint and type-check everything
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd $(DESKTOP) && npm run lint --if-present && npx tsc --noEmit

verify-openai: ## Probe the live OpenAI API (requires OPENAI_API_KEY)
	cd $(BACKEND) && uv run --env-file ../../.env python ../../scripts/verify_openai.py

clean:
	rm -rf $(CAPTURE)/.build $(BACKEND)/.venv $(DESKTOP)/node_modules
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
