# Lunch Money API Validation Notes

## Manual Accounts - Valid Fields

Based on actual API responses and testing:

### Account Type + Subtype
- Valid `type` values (10 types):
  - `cash`
  - `credit`
  - `cryptocurrency`
  - `employee compensation`
  - `investment`
  - `loan`
  - `other liability`
  - `other asset`
  - `real estate`
  - `vehicle`

- Valid `subtype` values (examples):
  - Under `type: "cash"`: `checking`, `savings`
  - Other types: mostly null/unspecified

### Required Fields for POST /manual_accounts
- `name` (string, max 45 chars for display)
- `type` (string, must be one of the 10 types above)
- `balance` (string, decimal format e.g. "0.0000")
- `currency` (string, ISO 4217 code e.g. "usd")

### Optional Fields
- `subtype` (string, type-specific)
- `external_id` (string, for deduplication) - format: `ynab:{budget_id}:{account_id}`
- `custom_metadata` (JSON object, max 4096 chars when stringified)
- `exclude_from_transactions` (boolean, for closed/inactive accounts)

### Invalid Fields (Do NOT Send)
- `closed_on` - requires ISO 8601 date, YNAB only provides boolean
- `status` - set via `exclude_from_transactions` instead
- `display_name` - read-only
- `balance_as_of` - read-only
- `created_at`, `updated_at` - read-only

## Known Issues & Fixes

### Issue: HTTP 400 "Invalid input" on closed accounts
- **Cause**: Sending `closed_on: true` (boolean) instead of ISO 8601 date
- **Fix**: Don't send `closed_on` if date unavailable; use `exclude_from_transactions: true` instead

### Issue: HTTP 400 on account type validation
- **Cause**: Using "checking", "savings" as account types (YNAB types, not LM types)
- **Fix**: Use `type: "cash"` with `subtype: "checking"` or `subtype: "savings"`

## OpenAPI Spec Validation

**Spec Location**: `docs/lunchmoney-api-v2.json` (v2.9.4)

All API endpoints used by the importer have been validated against the OpenAPI spec:
- ✓ GET /manual_accounts
- ✓ GET /plaid_accounts  
- ✓ GET /categories
- ✓ GET /transactions
- ✓ POST /manual_accounts
- ✓ PUT /manual_accounts/{id}
- ✓ POST /categories
- ✓ PUT /categories/{id}
- ✓ POST /transactions
- ✓ PUT /budgets

### Running Validation

```bash
.venv/bin/python3 importer/validate_api_calls.py
```

### Request Validation Results

**POST /manual_accounts**: ✓ Valid
- All required fields present: `name`, `type`, `balance`
- All optional fields correctly typed

**POST /transactions**: ✓ Valid
- Request schema with transaction array validated
- Supports `apply_rules`, `skip_duplicates`, `skip_balance_update` options

### Response Validation

The GET responses include server-side fields that are present in real API responses but not always needed in test data:
- Manual accounts: `display_name`, `status`, `created_at`, `updated_at`, etc. (read-only)
- Transactions: `id`, `status`, `created_at`, `updated_at`, `source`, etc. (read-only)
