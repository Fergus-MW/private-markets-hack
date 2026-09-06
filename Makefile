# Local development. Cloud deployment stays in infrastructure/ (Terraform).
.DEFAULT_GOAL := help
.PHONY: help env up down restart logs ps smoke test test-ingestion test-connectors test-mail test-frontend test-infra tf clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
COMPOSE := docker compose
# Pinned subset: the whole file pulls torch and unstructured, which local unit
# tests never touch. Versions come from requirements.txt so they cannot drift.
TEST_PINS := '^(fastapi|pydantic|openpyxl|httpx|python-multipart|pandas|numpy|google-auth|requests)=='

help: ## Show these targets
	@grep -hE '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sort | awk -F':.*##' '{printf "  \033[36m%-18s\033[0m%s\n", $$1, $$2}'

env: ## Ensure .env has a SESSION_KEY, appending one without touching other values
	@touch .env
	@# A file with no trailing newline would glue the key onto the last value.
	@[ ! -s .env ] || [ "$$(tail -c1 .env | wc -l)" -eq 1 ] || printf '\n' >> .env
	@grep -q '^SESSION_KEY=.' .env || { \
		printf 'SESSION_KEY=%s\n' "$$($(or $(PYTHON),python3) -c 'import secrets; print(secrets.token_hex(32))')" >> .env; \
		echo "Appended a new SESSION_KEY to .env. Add GOOGLE_OAUTH_* to enable sign-in."; }

up: env ## Build and start the local stack (frontend 18081, ingestion 18080, db 18000)
	$(COMPOSE) up --build -d
	@echo "Waiting for ingestion…"
	@for i in $$(seq 1 90); do curl -fsS http://localhost:18080/readyz >/dev/null 2>&1 && break || sleep 2; done
	@curl -fsS http://localhost:18080/readyz >/dev/null && echo "ingestion ready: http://localhost:18080" || (echo "ingestion did not become ready; try 'make logs'" && exit 1)
	@curl -fsS http://localhost:18081/healthz >/dev/null && echo "frontend ready:  http://localhost:18081" || echo "frontend not ready; try 'make logs'"

down: ## Stop containers, keep the database volume
	$(COMPOSE) down

restart: down up ## Rebuild and restart

logs: ## Follow logs from every service
	$(COMPOSE) logs -f

ps: ## Show container status
	$(COMPOSE) ps

smoke: ## Run the ingestion smoke test against the running stack
	$(COMPOSE) cp services/ingestion/tests/smoke.py ingestion:/tmp/smoke.py
	$(COMPOSE) exec -e PYTHONPATH=/app ingestion python /tmp/smoke.py

$(VENV): ## Create the local test virtualenv
	$(or $(PYTHON),python3) -m venv $(VENV)
	@$(PIP) install -q --upgrade pip

test: test-ingestion test-connectors test-mail test-infra test-frontend ## Run every test suite

test-ingestion: $(VENV) ## Ingestion unit tests
	@$(PIP) install -q $$(grep -E $(TEST_PINS) services/ingestion/requirements.txt)
	PYTHONPATH=services/ingestion $(PY) -m unittest discover -s services/ingestion/tests -v

test-connectors: $(VENV) ## Connector unit tests
	@$(PIP) install -q -r services/connectors/requirements.txt
	PYTHONPATH=services/connectors $(PY) -m unittest discover -s services/connectors/tests -v

test-mail: $(VENV) ## Mail agent unit tests
	@$(PIP) install -q -r services/mail_agent/requirements.txt
	cd services/mail_agent && PYTHONPATH=.:tests ../../$(PY) -m unittest discover -s tests -v

test-infra: $(VENV) ## Infrastructure bootstrap tests
	PYTHONPATH=infrastructure/scripts $(PY) -m unittest discover -s infrastructure/tests -v

test-frontend: ## Frontend tests and production build
	cd frontend && npm ci && npm test && npm run build

tf: ## Terraform format check and validate
	terraform -chdir=infrastructure fmt -check -recursive
	terraform -chdir=infrastructure init -backend=false -input=false
	terraform -chdir=infrastructure validate

clean: ## Stop the stack and DELETE the local database volume
	$(COMPOSE) down -v
	rm -rf $(VENV)
