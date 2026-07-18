.DEFAULT_GOAL := help
.PHONY: help

help: ## Show this help message
	@echo "Empirica Testing & Development Commands"
	@echo "========================================"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

# Installation
.PHONY: install
install: ## Install package with all dependencies
	pip install -e ".[dev,mcp]"

.PHONY: install-test
install-test: ## Install only test dependencies
	pip install -e ".[test]"

.PHONY: install-lint
install-lint: ## Install only linting dependencies
	pip install -e ".[lint]"

# Testing
.PHONY: test
test: ## Run all tests
	pytest tests/

.PHONY: test-unit
test-unit: ## Run unit tests only
	pytest tests/unit/ -v

.PHONY: test-integration
test-integration: ## Run integration tests only
	pytest tests/integration/ -m integration -v

.PHONY: test-integrity
test-integrity: ## Run integrity tests (framework principles validation)
	pytest tests/integrity/ -m integrity -v

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	pytest tests/ --cov=empirica --cov=empirica-mcp --cov-report=html --cov-report=term-missing

.PHONY: test-fast
test-fast: ## Run tests excluding slow tests
	pytest tests/ -m "not slow"

.PHONY: test-watch
test-watch: ## Run tests in watch mode (requires pytest-watch)
	pytest-watch tests/

# Code Quality
.PHONY: format
format: ## Format code with ruff
	ruff format empirica/ empirica-mcp/ tests/

.PHONY: format-check
format-check: ## Check code formatting without modifying
	ruff format --check empirica/ empirica-mcp/ tests/

.PHONY: lint
lint: ## Lint code with ruff
	ruff check empirica/ empirica-mcp/ tests/

.PHONY: lint-fix
lint-fix: ## Lint and auto-fix issues
	ruff check --fix empirica/ empirica-mcp/ tests/

.PHONY: typecheck
typecheck: ## Run type checking with pyright
	pyright empirica/ empirica-mcp/

.PHONY: check
check: format-check lint typecheck ## Run all code quality checks

.PHONY: check-fix
check-fix: format lint-fix typecheck ## Format, fix linting issues, and typecheck

.PHONY: install-hooks
install-hooks: ## Install the CI-parity pre-push ruff gate (composes with beads)
	@bash scripts/git-hooks/install.sh

# Release Validation
.PHONY: validate
validate: format lint typecheck test-integrity test-unit ## Validate release readiness (format + lint + typecheck + tests)
	@echo ""
	@echo "✅ Release validation complete!"
	@echo ""

.PHONY: validate-full
validate-full: format lint typecheck test-cov ## Full validation with coverage
	@echo ""
	@echo "✅ Full release validation complete!"
	@echo "📊 Coverage report: htmlcov/index.html"
	@echo ""

# Cleanup
.PHONY: clean
clean: ## Clean up generated files
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf htmlcov/ .coverage coverage.json
	rm -rf dist/ build/

.PHONY: clean-logs
clean-logs: ## Clean up test logs
	rm -rf .empirica_reflex_logs_test/
	rm -rf .empirica_test/

# Development
.PHONY: dev
dev: install format lint-fix ## Set up development environment

.PHONY: pre-commit
pre-commit: format lint-fix typecheck test-fast ## Run pre-commit checks (fast)

.PHONY: ci
ci: check test-cov ## Run CI pipeline locally

# Documentation
.PHONY: docs
docs: ## Generate documentation (if applicable)
	@echo "Documentation generation not yet implemented"

# Quick Commands
.PHONY: quick-test
quick-test: ## Quick test run (unit tests only, no coverage)
	pytest tests/unit/ -v --no-cov

.PHONY: quick-check
quick-check: lint-fix typecheck ## Quick check (fix lint + typecheck)

# Stats
.PHONY: stats
stats: ## Show project statistics
	@echo "=== Empirica Project Statistics ==="
	@echo ""
	@echo "Lines of Code:"
	@find empirica empirica-mcp -name "*.py" | xargs wc -l | tail -1
	@echo ""
	@echo "Test Files:"
	@find tests -name "test_*.py" | wc -l
	@echo ""
	@echo "Test Functions:"
	@grep -r "def test_" tests/ | wc -l
	@echo ""
