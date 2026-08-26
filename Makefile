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
	@echo "  check             Full CI: lint, typecheck, deps-check, test"
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
	docker compose -f docker-compose.test.yaml up -d --wait
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
check: lint typecheck deps-check test

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
