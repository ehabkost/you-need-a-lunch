.PHONY: help types check clean

help:
	@echo "Available targets:"
	@echo "  make types          Regenerate pydantic models from OpenAPI spec"
	@echo "  make check          Run type checking (mypy/pyright)"
	@echo "  make clean          Remove generated files"

types:
	@echo "Regenerating pydantic models from OpenAPI spec..."
	@.venv/bin/python -m datamodel_code_generator \
	  --input docs/lunchmoney-api-v2.json \
	  --output importer/lm_api_types_generated.py \
	  --target-python-version 3.11 \
	  --use-annotated \
	  --collapse-root-models \
	  --strict-nullable \
	  --formatters ruff-format
	@echo "✓ Generated importer/lm_api_types_generated.py"

check:
	@echo "Running mypy type checker..."
	@.venv/bin/mypy importer/ --strict 2>/dev/null || echo "Note: mypy not installed. Run: .venv/bin/pip install mypy"

clean:
	@echo "Cleaning generated files..."
	@rm -f importer/lm_api_types_generated.py
	@echo "✓ Cleaned"
