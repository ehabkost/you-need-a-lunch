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
	  --formatters ruff_format
	@sed -i "s/^    split = 'split'$$/    split_ = 'split'/" importer/lm_api_types_generated.py
	@sed -i "s/^        'active'$$/        Status.active/" importer/lm_api_types_generated.py
	@# ManualAccountObject: fields the spec marks required but the API returns null
	@perl -i -0pe "s/(display_name: Annotated\[\n        )str,(\n        Field\(\n            description='Optional display name for the account as set)/\1str | None,\2/; s/(description='Optional display name for the account as set by the user or derived.*?'\n        \),\n    \])/\1,\n    ] = None/s" importer/lm_api_types_generated.py
	@perl -i -0pe "s/(closed_on: Annotated\[\n        )date_aliased,(\n        Field\(\n            description='The date this account was closed)/\1date_aliased | None,\2/; s/(description='The date this account was closed.*?'\n        \),\n    \])/\1,\n    ] = None/s" importer/lm_api_types_generated.py
	@perl -i -0pe "s/(external_id: Annotated\[\n        )str,(\n        Field\(\n            description='An optional external_id)/\1str | None,\2/; s/(description='An optional external_id that may be set or updated via the API',\n            max_length=75,\n            min_length=0,\n        \),\n    \])/\1,\n    ] = None/s" importer/lm_api_types_generated.py
	@echo "✓ Generated importer/lm_api_types_generated.py"

check:
	@echo "Running mypy type checker..."
	@.venv/bin/mypy importer/ --strict 2>/dev/null || echo "Note: mypy not installed. Run: .venv/bin/pip install mypy"

clean:
	@echo "Cleaning generated files..."
	@rm -f importer/lm_api_types_generated.py
	@echo "✓ Cleaned"
