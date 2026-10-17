SHELL := /bin/sh
BACKEND := packages/backend
CAPTURE := packages/audio-capture
DESKTOP := packages/desktop

.PHONY: help setup test lint backend dev bundle capture-build capture-build-universal freeze-backend freeze-backend-x86 bundle-universal release release-universal capture-selftest stability verify-openai smoke-stt eval-translation clean doctor

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

capture-build: ## Build the Swift audio helper (current architecture)
	cd $(CAPTURE) && swift build -c release

capture-build-universal: ## Build the Swift helper for arm64 and x86_64
	@mkdir -p $(CAPTURE)/.build/universal
	cd $(CAPTURE) && swiftc -O -whole-module-optimization -target arm64-apple-macosx13.0 \
		Sources/TeamsAudioTap/*.swift -o .build/universal/tap-arm64
	cd $(CAPTURE) && swiftc -O -whole-module-optimization -target x86_64-apple-macosx13.0 \
		Sources/TeamsAudioTap/*.swift -o .build/universal/tap-x86_64
	cd $(CAPTURE) && lipo -create -output .build/universal/teams-audio-tap \
		.build/universal/tap-arm64 .build/universal/tap-x86_64
	@lipo -info $(CAPTURE)/.build/universal/teams-audio-tap

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

freeze-backend: ## Freeze the backend for this machine's architecture
	cd $(BACKEND) && uv run pyinstaller --clean --noconfirm jlt-backend.spec

freeze-backend-x86: ## Freeze the backend for Intel, using an x86_64 Python
	./scripts/freeze_backend_x86.sh

bundle: capture-build freeze-backend ## Build a self-contained .app for this machine
	cd $(DESKTOP) && npm run tauri build

bundle-universal: capture-build-universal freeze-backend freeze-backend-x86 ## Build for Apple Silicon and Intel
	cd $(DESKTOP) && npm run tauri build -- --target universal-apple-darwin

release: bundle ## Build, sign ad-hoc and package a shareable .dmg into dist/
	./scripts/package_release.sh

release-universal: bundle-universal ## Same, but running on both architectures
	UNIVERSAL=1 ./scripts/package_release.sh

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
