.DEFAULT_GOAL := help
SHELL := bash

PYTHON ?= python3
VENV := .venv
VERSION := $(shell sed -n 's/^VERSION = "\(.*\)"/\1/p' orca_git_sync.py)
HEADER_VERSION := $(shell sed -n 's/^. version = "\(.*\)"/\1/p' orca_git_sync.py)
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
	@grep -n $$'\xe2\x80\x94' README.md CHANGELOG.md orca_git_sync.py tests/*.py ; test $$? -eq 1
	@grep -n ';' README.md CHANGELOG.md ; test $$? -eq 1

.PHONY: ci
ci: setup lint test release ## What CI runs on GitHub and Forgejo: setup, lint, test, release build

.PHONY: fmt
fmt: ## Rewrite files with ruff format
	$(VENV)/bin/ruff format .

.PHONY: build
build: ## Build dist/ with the installable .py and a release zip
	mkdir -p $(DIST)
	cp orca_git_sync.py $(DIST)/
	cp orca_git_sync.py $(DIST)/orca_git_sync_any.py
	rm -f $(DIST)/orca-git-sync-$(VERSION).zip
	$(PYTHON) scripts/zip.py $(DIST)/orca-git-sync-$(VERSION).zip orca_git_sync.py README.md LICENSE
	@echo "install $(DIST)/orca_git_sync.py from Orca > Plugins > Install local plugin"

##@ Release

.PHONY: version-check
version-check: ## Fail unless the plugin header, VERSION and TAG (env, vX.Y.Z) agree
	@test -n "$(VERSION)" && test "$(VERSION)" = "$(HEADER_VERSION)" || { echo "header $(HEADER_VERSION), VERSION $(VERSION)"; exit 1; }
	@tag="$${TAG-v$(VERSION)}"; [[ "$$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$$ ]] || { echo "TAG must look like v1.2.3" >&2; exit 1; }; \
	  test "$$tag" = "v$(VERSION)" || { echo "tag $$tag does not match v$(VERSION)" >&2; exit 1; }

.PHONY: release
release: version-check build ## Build dist/ plus release notes from CHANGELOG.md (CI runs it on a published release)
	awk -v v="$(VERSION)" '/^## /{f=($$2==v); next} f{b=b $$0 "\n"} END{sub(/^\n+/,"",b); sub(/\n+$$/,"\n",b); printf "%s", b}' CHANGELOG.md > $(DIST)/notes.md
	@test -s $(DIST)/notes.md || { echo "CHANGELOG.md has no section for $(VERSION)"; exit 1; }
	{ cat $(DIST)/notes.md; printf '\nInstall: download `orca_git_sync.py`, Orca > Plugins > Browse plugins > Install local plugin. With an Orca Cloud account: Browse plugins, search Git Sync, Subscribe.\n'; } > $(DIST)/release-notes.md

##@ Housekeeping

.PHONY: clean
clean: ## Remove dist and caches
	rm -rf $(DIST) __pycache__ tests/__pycache__ .ruff_cache
