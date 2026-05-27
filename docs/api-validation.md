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

## Testing Against Spec

User-provided OpenAPI spec references:
- `blob:https://alpha.lunchmoney.dev/a9804836-2369-4b86-975d-e558e55e9dc1`
- `blob:https://alpha.lunchmoney.dev/29279db6-3a0e-46b1-9a34-254815b5380b`

These should be checked against the actual implementation for complete validation.
