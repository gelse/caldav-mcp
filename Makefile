IMAGE_NAME := caldav-mcp
IMAGE_TAG := latest

.PHONY: build
build:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

.PHONY: test
test:
	./.venv/bin/pytest tests/

.PHONY: lint
lint: ; ./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .

.PHONY: typecheck
typecheck: ; ./.venv/bin/mypy server.py caldav_mcp/

.PHONY: deps-check
deps-check:
	@echo "Checking dependency consistency …"
	@# Extract pinned runtime deps from pyproject.toml (lines inside dependencies = [...])
	@PY_DEPS=$$(python3 -c "\
	 import tomllib, json; \
	 f=open('pyproject.toml','rb'); \
	 d=tomllib.load(f); \
	 print(json.dumps(sorted(d['project']['dependencies'])))" 2>/dev/null || \
	 python3 -c "\
	 import toml; \
	 d=toml.load('pyproject.toml'); \
	 print(json.dumps(sorted(d['project']['dependencies'])))" 2>/dev/null); \
	REQ_DEPS=$$(python3 -c "import json; lines=[l.strip() for l in open('requirements.txt') if l.strip()]; print(json.dumps(sorted(lines)))"); \
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
