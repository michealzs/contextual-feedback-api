.DEFAULT_GOAL := help
IMAGE ?= contextual-feedback:latest

.PHONY: help install run test lint fmt docker-build docker-up docker-down \
        monitoring-up monitoring-down clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install with dev extras
	pip install -e ".[dev]"

run: ## Run the dev server
	python -m contextual_feedback

test: ## Run the test suite
	pytest -q

lint: ## Lint with ruff
	ruff check .

fmt: ## Auto-fix lint issues
	ruff check --fix .

docker-build: ## Build the Docker image
	docker build -t $(IMAGE) .

docker-up: ## Start the API container
	docker compose up -d --build

docker-down: ## Stop the API container
	docker compose down

monitoring-up: ## Start app + Prometheus + Grafana
	docker compose --profile monitoring up -d --build

monitoring-down: ## Stop the full stack
	docker compose --profile monitoring down

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache **/__pycache__ build dist *.egg-info data uploads logs
