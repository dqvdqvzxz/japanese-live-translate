SHELL := /bin/sh
BACKEND := packages/backend
CAPTURE := packages/audio-capture
DESKTOP := packages/desktop

.PHONY: help setup test lint backend dev bundle capture-build freeze-backend capture-selftest stability verify-openai smoke-stt eval-translation clean doctor

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
	cd $(BACKEND) && uv run ruff check . ../../scripts && uv run ruff format --check . ../../scripts && uv run mypy
	cd $(DESKTOP) && npm run lint --if-present && npx tsc --noEmit
	cd $(DESKTOP)/src-tauri && cargo fmt --check && cargo clippy -- -D warnings

backend: ## Run the backend on its own (prints its port and token)
	cd $(BACKEND) && uv run --env-file ../../.env python -m jlt

dev: ## Run the full desktop app (Tauri spawns the backend itself)
	cd $(DESKTOP) && npm run tauri dev

freeze-backend: ## Freeze the backend into a standalone executable
	cd $(BACKEND) && uv run pyinstaller --clean --noconfirm jlt-backend.spec

bundle: capture-build freeze-backend ## Build a macOS .app and .dmg (self-contained)
	cd $(DESKTOP) && npm run tauri build

stability: ## Long-running stability check (make stability MINUTES=60)
	./scripts/stability_run.sh $(MINUTES)

verify-openai: ## Probe the live OpenAI API (requires OPENAI_API_KEY)
	cd $(BACKEND) && uv run --env-file ../../.env python ../../scripts/verify_openai.py

smoke-stt: ## Live STT round-trip with synthesised Japanese (requires .env)
	cd $(BACKEND) && uv run --env-file ../../.env python ../../scripts/smoke_stt.py

eval-translation: ## Run the golden dataset against the live model (requires .env)
	cd $(BACKEND) && uv run --env-file ../../.env python ../../scripts/eval_translation.py

clean:
	rm -rf $(CAPTURE)/.build $(BACKEND)/.venv $(DESKTOP)/node_modules $(DESKTOP)/src-tauri/target
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
