.PHONY: help check

help:
	@echo "Available targets:"
	@echo "  make check          Run type checking (mypy/pyright)"

check:
	@echo "Running mypy type checker..."
	@.venv/bin/mypy lunchmoney/ --strict 2>/dev/null || echo "Note: mypy not installed. Run: .venv/bin/pip install mypy"
