.DEFAULT_GOAL := help
SHELL := bash

PYTHON ?= python3
VENV := .venv
VERSION := $(shell sed -n 's/^VERSION = "\(.*\)"/\1/p' orca_git_sync.py)
DIST := dist

##@ General

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nusage: make <target>\n"} \
	  /^[a-zA-Z0-9_.-]+:.*##/ { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 } \
	  /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)

##@ Development

.PHONY: setup
setup: ## Create .venv with the lint tools
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q ruff

.PHONY: test
test: ## Run the unit and two-Mac sync tests (needs git, no Orca)
	$(PYTHON) -m unittest discover -s tests -v

.PHONY: lint
lint: ## Ruff, plus no em dashes or semicolons in prose
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	@grep -n $$'\xe2\x80\x94' README.md orca_git_sync.py tests/*.py ; test $$? -eq 1
	@grep -n ';' README.md ; test $$? -eq 1

.PHONY: fmt
fmt: ## Rewrite files with ruff format
	$(VENV)/bin/ruff format .

.PHONY: build
build: ## Build dist/ with the installable .py and a release zip
	mkdir -p $(DIST)
	cp orca_git_sync.py $(DIST)/
	rm -f $(DIST)/orca-git-sync-$(VERSION).zip
	zip -q -j $(DIST)/orca-git-sync-$(VERSION).zip orca_git_sync.py README.md LICENSE
	@echo "install $(DIST)/orca_git_sync.py from Orca > Plugins > Install local plugin"

##@ Housekeeping

.PHONY: clean
clean: ## Remove dist and caches
	rm -rf $(DIST) __pycache__ tests/__pycache__ .ruff_cache
