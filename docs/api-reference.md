# API Reference: YNAB and Lunch Money

## YNAB API

Base URL: `https://api.ynab.com/v1`

### Data Types (export order)

#### 0.1 Accounts
- Fields: `id`, `name`, `type` (checking/savings/cash/creditCard/otherAsset/otherLiability), `on_budget`, `closed`, `note`, `balance`, `cleared_balance`, `uncleared_balance`, `transfer_payee_id`, `direct_import_linked`, `direct_import_in_error`, `last_reconciled_at`, `debt_original_balance`, `debt_interest_rates`, `debt_minimum_payments`, `debt_escrow_amounts`, `deleted`
- Amounts in **milliunits** (1000 milliunits = 1 currency unit)
- Endpoint: `GET /plans/{plan_id}/accounts`

#### 0.2 Payees
- Fields: `id`, `name`, `transfer_account_id`, `deleted`
- Endpoint: `GET /plans/{plan_id}/payees`

#### 0.3 Category Groups and Categories
- Groups: `id`, `name`, `hidden`, `internal`, `deleted`, `categories[]`
- Categories: `id`, `category_group_id`, `category_group_name`, `name`, `hidden`, `internal`, `note`, `budgeted`, `activity`, `balance`, `goal_type`, `goal_*` (many goal fields), `deleted`
- Goal types: `TB` (target balance), `TBD` (target balance by date), `MF` (monthly funding), `NEED` (needed for spending), `DEBT`
- Endpoint: `GET /plans/{plan_id}/categories`

#### 1. Transactions
- Fields: `id`, `account_id`, `account_name`, `payee_id`, `payee_name`, `category_id`, `category_name`, `approved`, `cleared` (uncleared/cleared/reconciled), `flag_color`, `date`, `amount` (milliunits, negative=outflow), `memo`, `deleted`, `subtransactions[]`, `transfer_account_id` (set when it's a transfer between accounts), `import_id` (external import deduplication key)
- Sub-transactions: `id`, `transaction_id`, `amount`, `memo`, `payee_id`, `payee_name`, `category_id`, `category_name`, `deleted`
- Endpoint: `GET /plans/{plan_id}/transactions`

#### 2. Budget Assignments (Monthly Category Budgets)
- Per-month, per-category: `budgeted`, `activity`, `balance` (all milliunits)
- Endpoint: `GET /plans/{plan_id}/months` → list months; `GET /plans/{plan_id}/months/{month}` → categories for that month
- Month summary also contains: `income`, `budgeted`, `activity`, `to_be_budgeted`, `age_of_money`

#### 3. Budget Goals
- Embedded on Category objects (see above): `goal_type`, `goal_target`, `goal_target_month`, `goal_target_date`, `goal_cadence`, `goal_cadence_frequency`, `goal_day`, `goal_creation_month`, `goal_needs_whole_amount`

#### 4. Scheduled Transactions
- Fields: `id`, `account_id`, `account_name`, `payee_id`, `payee_name`, `category_id`, `category_name`, `frequency`, `amount` (milliunits), `memo`, `flag_color`, `first_date`, `next_due_date`, `deleted`, `subtransactions[]`
- Frequencies: never/daily/weekly/everyOtherWeek/twiceAMonth/every4Weeks/monthly/everyOtherMonth/every3Months/every4Months/twiceAYear/yearly/everyOtherYear
- Endpoint: `GET /plans/{plan_id}/scheduled_transactions`

#### Other YNAB data
- **Money Movements / Money Movement Groups**: internal fund movements between categories (used by the YNAB budgeting methodology). Endpoints: `GET /plans/{plan_id}/money_movements`, `GET /plans/{plan_id}/money_movement_groups`. Export for reference, but Lunch Money has no direct equivalent.
- **Payee Locations**: geographic data for payees (rarely populated). Endpoint: `GET /plans/{plan_id}/payee_locations`.

### YNAB Amount Convention
- Milliunits: divide by 1000 to get currency units
- **Negative = outflow** (expense/money leaving account)
- **Positive = inflow** (income/deposit into account)

### Authentication & Rate Limits
- Bearer token via `Authorization: Bearer TOKEN`
- Rate limit: 200 requests/hour per token

## Lunch Money v2 API

Base URL: `https://api.lunchmoney.dev/v2`

### Resource Types

| Resource | Endpoint |
|---|---|
| Current user | `GET /me` |
| Categories | `GET/POST/PUT /categories`, `DELETE /categories/:id` |
| Tags | `GET /tags` |
| Transactions | `GET/POST/PUT /transactions`, `DELETE /transactions/:id` |
| Manual accounts | `GET/POST/PUT/DELETE /manual_accounts` (formerly "assets" in v1) |
| Plaid accounts | `GET /plaid_accounts` (read-only) |
| Recurring items | `GET /recurring_items` |
| Budget | `GET /budget` (implied from v1) |
| Crypto | `GET /crypto` |

### Amount Convention (v2)
- Decimal strings, up to 4 decimal places (e.g. `"50.0000"`)
- **Positive = debit** (expense/money going out)
- **Negative = credit** (income/money coming in)
- This is the **opposite sign** of YNAB — conversion required
- `to_base`: read-only float, converts to user's primary currency; do not send in POST/PUT

### Manual Accounts (formerly Assets)
- Fields: `id`, `name`, `type`, `subtype`, `balance`, `currency`, `external_id`, `custom_metadata`, `exclude_from_transactions`, `closed_on`
- Valid `type` values: `cash`, `credit`, `cryptocurrency`, `employee compensation`, `investment`, `loan`, `other liability`, `other asset`, `real estate`, `vehicle`
  - Note: YNAB's `checking` and `savings` types map to `other asset` (LM has no dedicated checking/savings type)
- `external_id`: **fully qualified YNAB account identifier** in format `ynab:{budget_id}:{account_id}` (e.g. `ynab:a1b2c3d4-e5f6-7890-abcd-ef1234567890:1a2b3c4d-5e6f-7890-abcd-ef1234567890`) for unambiguous deduplication when re-importing or importing the same budget to multiple LM accounts
- `custom_metadata`: freeform JSON; use to store additional YNAB account info (e.g. `{"ynab_name": "...", "ynab_type": "..."}`) that may be useful for debugging or re-runs
- `closed_on`: optional ISO 8601 date indicating when the account was closed (prevents future activity)

### Plaid Accounts
- Fields: `id`, `name`, `type`, `subtype`, `balance`, `currency`, `allow_transaction_modification`
- `allow_transaction_modification`: when `false`, the account is fully bank-synced and LM blocks manual transaction changes — do not import transactions into these accounts. When `true`, LM permits manual transaction additions even though the account has a Plaid connection (e.g. connection lost but not yet converted back to manual) — importing into these is allowed.
- **No `external_id` or `custom_metadata`** — Plaid accounts have no writable metadata fields; YNAB↔Plaid mapping must be stored locally in the mapping file

#### Merging manual accounts with Plaid accounts
Lunch Money supports merging a manually-managed account with a synced (Plaid) account via the UI, but:
- **The merge is irreversible** — once merged it cannot be undone
- It is a manual step the user must perform in the web UI; there is no API for it

**Recommended strategy**: require the user to set up Plaid bank sync in Lunch Money *before* running the importer for any accounts that are `direct_import_linked: true` in YNAB. The importer then skips creating a manual account for those and instead matches transactions to the existing Plaid account. This avoids the merge step entirely.

### Transactions
- Fields: `id`, `amount`, `currency`, `payee`, `category_id`, `manual_account_id`, `tag_ids`, `is_split_parent`, `split_parent_id`, `created_at`, `updated_at`
- **Deduplication**: POST returns a `skipped_duplicates` array listing conflicts with `existing_transaction_id` and reason
- **Partial insertion**: non-duplicate transactions are inserted even if some in the batch are duplicates
- Use `custom_metadata` to store YNAB transaction UUID for future deduplication checks

### Categories
- Lunch Money uses flat categories (no sub-groups exposed in the same way as YNAB)
- Map YNAB category groups → Lunch Money category groups where possible

### Authentication
- Bearer token via `Authorization: Bearer TOKEN`
