IMAGE_NAME := caldav-mcp
IMAGE_TAG := latest

# Active Python interpreter; prefer local venv, fall back to system python3
PYTHON ?= $(if $(wildcard .venv/bin/python3),.venv/bin/python3,python3)

.PHONY: help
help:
	@echo "make <target>"
	@echo ""
	@echo "Targets:"
	@echo "  build             Build Docker image"
	@echo "  test              Run unit tests"
	@echo "  test-unit         Run unit tests"
	@echo "  test-integration  Run integration tests"
	@echo "  test-performance  Run performance tests"
	@echo "  test-all          Run all tests"
	@echo "  lint              Lint with ruff"
	@echo "  typecheck         Type check with mypy"
	@echo "  deps-check        Check dependency consistency"
	@echo "  deps-update       Sync requirements.txt from pyproject.toml"
	@echo "  deps-verify       Verify dependency consistency (exit code only)"
	@echo "  ui-install        npm ci for the admin UI (requires Node >= 20.19 + npm)"
	@echo "  ui-build          Build the admin UI into ui/dist"
	@echo "  ui-test           Run admin UI tests (vitest)"
	@echo "  ui-lint           Lint admin UI (eslint)"
	@echo "  ui-typecheck      Type check admin UI (svelte-check)"
	@echo "  ui-check          Full UI gate: ui-lint, ui-typecheck, ui-test, ui-build"
	@echo "                     (ui-build runs the post-build CSP-construction check)"
	@echo "  check             Full CI: lint, typecheck, deps-check, test, ui-check"
	@echo "                     (ui-check needs Node >= 20.19 — same class of"
	@echo "                     prerequisite as Docker for test-integration)"
	@echo "  docs-serve        Print instructions for viewing docs"
	@echo "  docs-check        Verify doc files exist"

.PHONY: build
build:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

.PHONY: test
test:
	$(PYTHON) -m pytest tests/unit/

.PHONY: test-unit
test-unit:
	$(PYTHON) -m pytest tests/unit/

.PHONY: test-integration
test-integration:
	@# Build the pro-mode SQLite store (needed by mcp-pro service)
	@$(PYTHON) tests/integration/build_pro_store.py
	@docker compose -f docker-compose.test.yaml up -d --build --wait
	$(PYTHON) -m pytest tests/integration/ -m integration --timeout=60; \
	EXIT=$$?; \
	docker compose -f docker-compose.test.yaml down -v; \
	exit $$EXIT

.PHONY: test-performance
test-performance:
	$(PYTHON) -m pytest tests/performance/ -m performance --benchmark-only

.PHONY: test-all
test-all: test-unit test-integration test-performance

.PHONY: lint
lint: ; $(PYTHON) -m ruff check . && $(PYTHON) -m ruff format --check .

.PHONY: typecheck
typecheck: ; $(PYTHON) -m mypy server.py caldav_mcp/

.PHONY: deps-check
deps-check:
	@echo "Checking dependency consistency …"
	@# Extract pinned runtime deps from pyproject.toml (lines inside dependencies = [...])
	@# Requires Python >= 3.11 (tomllib is stdlib); use $(PYTHON) so the venv interpreter is used.
	@PY_DEPS=$$($(PYTHON) -c "import tomllib, json; print(json.dumps(sorted(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies'])))"); \
	REQ_DEPS=$$($(PYTHON) -c "import json; lines=[l.strip() for l in open('requirements.txt') if l.strip()]; print(json.dumps(sorted(lines)))"); \
	if [ "$$PY_DEPS" = "$$REQ_DEPS" ]; then \
	 echo "✓ dependencies match"; \
	else \
	 echo "✗ MISMATCH between pyproject.toml and requirements.txt"; \
	 echo "  pyproject.toml: $$PY_DEPS"; \
	 echo "  requirements.txt: $$REQ_DEPS"; \
	 exit 1; \
	fi

.PHONY: check
check: lint typecheck deps-check test ui-check

# ---------------------------------------------------------------------------
# Admin UI (ui/) — every target depends on ui-install so a fresh checkout
# without node_modules cannot fail with an opaque "eslint: not found"-class
# error. Node >= 20.19 (Vite 7's floor) is a hard prerequisite from UI-M2.
# ---------------------------------------------------------------------------
.PHONY: ui-install
ui-install:
	@command -v node >/dev/null 2>&1 || { echo "✗ Node.js 20.19+ (with npm) is required for UI targets — install it from https://nodejs.org"; exit 1; }
	@node -e 'const [maj,min]=process.versions.node.split(".").map(Number); if (maj<20||(maj===20&&min<19)) { console.error("✗ Node.js 20.19+ is required for UI targets, found "+process.version); process.exit(1); }'
	@command -v npm >/dev/null 2>&1 || { echo "✗ Node.js 20.19+ (with npm) is required for UI targets — npm not found"; exit 1; }
	cd ui && npm ci

.PHONY: ui-build
ui-build: ui-install
	cd ui && npm run build

.PHONY: ui-test
ui-test: ui-install
	cd ui && npm run test -- --run

.PHONY: ui-lint
ui-lint: ui-install
	cd ui && npm run lint

.PHONY: ui-typecheck
ui-typecheck: ui-install
	cd ui && npm run typecheck

.PHONY: ui-check
# ui-build last: `npm run build` fires the postbuild CSP-construction gate
# (no inline <script>/style= in ui/dist/index.html), so every `make check`
# also proves the built output stays strict-CSP-compatible.
ui-check: ui-lint ui-typecheck ui-test ui-build

.PHONY: docs-serve
docs-serve:
	@echo "Opening docs/ directory — view with any Markdown renderer"
	@echo "  e.g. code docs/architecture.md"

.PHONY: deps-update
deps-update:
	@echo "Syncing requirements.txt with pyproject.toml …"
	@$(PYTHON) -c "\
	import tomllib, json; \
	py_deps = sorted(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']); \
	open('requirements.txt','w').write('\n'.join(py_deps) + '\n'); \
	print('✓ requirements.txt updated')"

.PHONY: deps-verify
deps-verify:
	@$(PYTHON) -c "\
	import tomllib; \
	py_deps = sorted(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']); \
	req_deps = sorted(l.strip() for l in open('requirements.txt') if l.strip()); \
	exit(0 if py_deps == req_deps else 1)"

.PHONY: docs-check
docs-check:
	@test -f docs/architecture.md && echo "✓ docs/architecture.md exists" || (echo "✗ docs/architecture.md missing" && exit 1)
	@test -f docs/api.md && echo "✓ docs/api.md exists" || (echo "✗ docs/api.md missing" && exit 1)
	@test -f docs/contributing.md && echo "✓ docs/contributing.md exists" || (echo "✗ docs/contributing.md missing" && exit 1)
	@test -f docs/cli.md && echo "✓ docs/cli.md exists" || (echo "✗ docs/cli.md missing" && exit 1)
	@test -f docs/ui.md && echo "✓ docs/ui.md exists" || (echo "✗ docs/ui.md missing" && exit 1)
