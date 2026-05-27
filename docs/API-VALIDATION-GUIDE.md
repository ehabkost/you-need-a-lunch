# API Validation Guide

This project uses the Lunch Money v2 OpenAPI spec to validate all API requests and responses.

## Overview

- **Spec Location**: `docs/lunchmoney-api-v2.json` (v2.9.4)
- **Validation Tools**: 
  - `jsonschema` — validates JSON against OpenAPI schemas
  - `openapi-core` — full request/response validation (advanced)

## Quick Validation

Run the schema validation tests:

```bash
.venv/bin/python3 lunchmoney/validate_api_calls.py
```

Expected output:
```
Results: 4 passed, 0 failed
```

## Tools Available

### 1. validate_api_calls.py (Schema Validation)
Validates request/response bodies against JSON schemas.

**Usage**: 
```bash
.venv/bin/python3 lunchmoney/validate_api_calls.py
```

**What it checks**:
- POST /manual_accounts request schema
- POST /transactions request schema
- GET /manual_accounts response schema
- GET /transactions response schema

### 2. lm_client_validated.py (Runtime Validation)
Wraps LMClient to validate every API call automatically.

**Usage**:
```python
from lm_client_validated import ValidatedLMClient

client = ValidatedLMClient(token="...")
manual_accounts = client.get_manual_accounts()  # validated
```

## Key Validation Rules

### Manual Accounts

**Required fields for POST**:
- `name` (string, 1-45 chars)
- `type` (one of 10 types)
- `balance` (decimal string, up to 4 decimals)

**Type values** (10 total):
```
cash, credit, cryptocurrency, employee compensation,
investment, loan, other asset, other liability, real estate, vehicle
```

**Never send** (read-only or incorrectly named):
- `closed_on` (boolean) — use `status: "closed"` + `closed_on: "2024-01-01"` instead
- `display_name` — auto-generated
- `status` — use `exclude_from_transactions` for closed accounts
- `created_at`, `updated_at` — server-managed

**Do send** (optional but recommended):
- `external_id` — format: `ynab:{budget_id}:{account_id}`
- `custom_metadata` — JSON object with YNAB type/flags
- `exclude_from_transactions` — boolean, for closed/inactive accounts

### Transactions

**Required fields for POST**:
- `transactions` — array of transaction objects

**Per transaction**:
- `date` — ISO 8601 date (e.g., "2024-01-01")
- `amount` — decimal string (e.g., "10.50")
- `currency` — ISO 4217 code (e.g., "usd")

**Deduplication via**:
- `custom_metadata.ynab_id` — external transaction ID
- `date` + `payee` + `amount` + `account_id` (if skip_duplicates enabled)

### Categories

**Required fields for POST**:
- `name` (string)
- `group_id` (integer)

**Never send**:
- `id` — server-assigned
- `created_at`, `updated_at` — server-managed

## Common Validation Errors

### HTTP 400: "Invalid input"
**Possible causes**:
1. Missing required field (e.g., `currency` in manual account)
2. Invalid enum value (e.g., `type: "checking"` instead of `type: "cash", subtype: "checking"`)
3. Invalid format (e.g., amount as number instead of string)
4. Too-long string (e.g., `name` > 45 chars)

**Solution**: Check the schema in `docs/lunchmoney-api-v2.json` for `POST /manual_accounts` or `/transactions`.

### Field Not Accepted
**Possible causes**:
1. Sending a read-only field (id, created_at, display_name, etc.)
2. Incorrect field name (e.g., `status` instead of `exclude_from_transactions`)

**Solution**: See "Never send" sections above.

## Regenerating the Spec

If you have an updated `api-1.json` or `api-1.yaml` from Lunch Money:

```bash
cp /path/to/api-1.json docs/lunchmoney-api-v2.json
# or
cp /path/to/api-1.yaml docs/lunchmoney-api-v2.json  # YAML will auto-convert
```

Then re-run validation tests to catch any breaking changes.

## References

- [Lunch Money Developer Portal](https://lunchmoney.dev/v2/introduction)
- [Lunch Money API Docs](https://lunchmoney.dev/v2/overview)
- [OpenAPI 3.0.2 Spec](https://spec.openapis.org/oas/v3.0.2)
