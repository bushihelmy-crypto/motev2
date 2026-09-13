SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := check

GO ?= go
GOFMT ?= gofmt
GO_DIR ?= src
BINARY ?= gateway
BIN_DIR ?= bin
VERSION ?= dev
COVERAGE_FILE ?= coverage.out
CACHE_DIR ?= $(CURDIR)/.cache
TOOLS_BIN ?= $(CURDIR)/.tools/bin

GOLANGCI_LINT_VERSION ?= v2.13.2
GOVULNCHECK_VERSION ?= v1.7.0
GOLANGCI_LINT_VERSION_NUMBER := $(patsubst v%,%,$(GOLANGCI_LINT_VERSION))
GOLANGCI_LINT ?= $(TOOLS_BIN)/golangci-lint
GOVULNCHECK ?= $(TOOLS_BIN)/govulncheck
GOLANGCI_LINT_CACHE ?= $(CACHE_DIR)/golangci-lint

GO_PACKAGE := ./cmd/gateway
BUILDINFO_PACKAGE := github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/buildinfo
LDFLAGS := -s -w -X $(BUILDINFO_PACKAGE).Version=$(VERSION)

GOCACHE ?= $(CACHE_DIR)/go-build
GOMODCACHE ?= $(CACHE_DIR)/go-mod
GOTOOLCHAIN ?= local
export GOCACHE GOMODCACHE GOTOOLCHAIN GOLANGCI_LINT_CACHE

# Some analysis tools invoke `go` by name internally. Put an explicitly
# selected binary first when a caller supplies one, so analysis cannot
# accidentally use another version found earlier on PATH.
GO_ENV = PATH="$$(dirname "$$(readlink -f "$$(command -v $(GO))")"):$${PATH}"

.PHONY: all help check quality check-structure toolchain-check format fmt fmt-check
.PHONY: vet lint tools test test-unit test-integration architecture complexity coverage
.PHONY: module-hygiene tidy-check security vulncheck secret-scan license-check build run
.PHONY: docker-build clean

all: check

help:
	@printf '%s\n' 'Mote Gateway (Go scaffold) commands:'
	@printf '%s\n' '  make check             complete local/CI quality gate'
	@printf '%s\n' '  make test-unit         deterministic unit tests + race detector'
	@printf '%s\n' '  make test-integration  local integration harness (integration tag)'
	@printf '%s\n' '  make architecture      package ownership and dependency checks'
	@printf '%s\n' '  make complexity        exact production complexity ratchet'
	@printf '%s\n' '  make tools             install pinned lint/security tools locally'
	@printf '%s\n' '  make build             reproducible CGO-free scaffold binary'
	@printf '%s\n' '  make docker-build      build the pinned-base scaffold image'
	@printf '%s\n' ''
	@printf '%s\n' 'Toolchain: src/go.mod and .go-version define the local baseline;'
	@printf '%s\n' '  CI owns the compatibility matrix.'

# This is the single project gate. Keep the phases explicit so a failure says
# which owner rejected the change instead of hiding everything behind one tool.
check: quality test-unit coverage test-integration build

quality: check-structure toolchain-check fmt-check vet lint architecture complexity module-hygiene security license-check

check-structure:
	@./scripts/check-structure.sh

toolchain-check:
	@GO_BIN='$(GO)' ./scripts/check-toolchain.sh

format fmt:
	@$(GOFMT) -w $$(find $(GO_DIR) -type f -name '*.go' -print)

fmt-check:
	@files="$$(find $(GO_DIR) -type f -name '*.go' -print)"; \
		if [ -n "$$files" ]; then \
			unformatted="$$(printf '%s\n' "$$files" | xargs $(GOFMT) -l)"; \
			if [ -n "$$unformatted" ]; then \
				echo 'Go files are not formatted; run make format'; \
				printf '%s\n' "$$unformatted"; \
				exit 1; \
			fi; \
		fi

vet:
	@$(GO) -C $(GO_DIR) vet ./...

tools:
	@mkdir -p $(TOOLS_BIN)
	@if [ ! -x '$(GOLANGCI_LINT)' ] || ! '$(GOLANGCI_LINT)' version 2>/dev/null | grep -Fq '$(GOLANGCI_LINT_VERSION_NUMBER)'; then \
		echo 'installing golangci-lint $(GOLANGCI_LINT_VERSION)'; \
		GOBIN='$(TOOLS_BIN)' $(GO_ENV) $(GO) install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@$(GOLANGCI_LINT_VERSION); \
	fi
	@if [ ! -x '$(GOVULNCHECK)' ] || ! '$(GOVULNCHECK)' -version 2>&1 | grep -Fq '$(GOVULNCHECK_VERSION)'; then \
		echo 'installing govulncheck $(GOVULNCHECK_VERSION)'; \
		GOBIN='$(TOOLS_BIN)' $(GO_ENV) $(GO) install golang.org/x/vuln/cmd/govulncheck@$(GOVULNCHECK_VERSION); \
	fi

lint: tools
	@cd $(GO_DIR) && $(GO_ENV) '$(GOLANGCI_LINT)' run --config ../.golangci.yaml ./...

test test-unit:
	@packages="$$(cd $(GO_DIR) && $(GO) list ./... | grep -Ev '/internal/(architecture|complexity)$$|/integration$$')"; \
		test -n "$$packages"; \
		$(GO) -C $(GO_DIR) test -race -count=1 -covermode=atomic -coverprofile=../$(COVERAGE_FILE) $$packages

test-integration:
	@$(GO) -C $(GO_DIR) test -race -count=1 -tags=integration ./integration/...

architecture:
	@$(GO) -C $(GO_DIR) test -count=1 ./internal/architecture

complexity:
	@$(GO) -C $(GO_DIR) test -count=1 ./internal/complexity

coverage: test-unit
	@$(GO) -C $(GO_DIR) tool cover -func=../$(COVERAGE_FILE)

module-hygiene tidy-check:
	@GO_BIN='$(GO)' ./scripts/check-module-hygiene.sh

security: tools secret-scan vulncheck

vulncheck:
	@cd $(GO_DIR) && $(GO_ENV) '$(GOVULNCHECK)' ./...

secret-scan:
	@./scripts/check-secrets.sh

license-check:
	@./scripts/check-license.sh

build:
	@mkdir -p $(BIN_DIR)
	@CGO_ENABLED=0 $(GO) -C $(GO_DIR) build -trimpath -ldflags '$(LDFLAGS)' -o ../$(BIN_DIR)/$(BINARY) $(GO_PACKAGE)

run:
	@$(GO) -C $(GO_DIR) run $(GO_PACKAGE)

docker-build:
	@docker build --build-arg VERSION=$(VERSION) -t mote-gateway:$(VERSION) .

clean:
	@rm -rf $(BIN_DIR) $(COVERAGE_FILE) $(CACHE_DIR) $(TOOLS_BIN)
