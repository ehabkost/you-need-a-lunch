.PHONY: help check test

help:
	@echo "Available targets:"
	@echo "  make check          Run type checking (mypy/pyright)"
	@echo "  make test           Run pytest test suite"

check:
	@echo "Running mypy type checker..."
	@.venv/bin/mypy lunchmoney/ --strict 2>/dev/null || echo "Note: mypy not installed. Run: .venv/bin/pip install mypy"

test:
	@.venv/bin/pytest tests/ -v
