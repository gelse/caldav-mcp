IMAGE_NAME := caldav-mcp
IMAGE_TAG := latest

# Active Python interpreter; set to a local venv for development (e.g. .venv/bin/python)
PYTHON ?= python3

.PHONY: build
build:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

.PHONY: test
test:
	$(PYTHON) -m pytest tests/

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
