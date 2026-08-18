IMAGE_NAME := caldav-mcp
IMAGE_TAG := latest

.PHONY: build
build:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

.PHONY: test
test:
	pytest tests/
