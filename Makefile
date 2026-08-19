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

.PHONY: check
check: lint typecheck test
