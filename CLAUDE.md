# you-need-a-lunch

A tool to migrate data from YNAB (You Need A Budget) to Lunchmoney.app using their respective APIs.

## Goal

Export all data from YNAB, then import it into Lunch Money — carefully avoiding duplication of any data already present. A summary of planned changes is always shown before applying them.

The tool is intentionally scoped to one-way migration for now. It is not a sync tool, and is not designed to be a generic importer/exporter for other services.

## Project Structure (planned)

```
exporter/   # reads from YNAB API, writes to local JSON files
importer/   # reads local JSON files, writes to Lunch Money API
data/       # intermediate export files (gitignored)
```

## Secrets and Authentication

All tools must read secrets exclusively from environment variables — no config files, no hardcoded values, no interactive prompts for credentials. Secrets are injected via `wsl-op-run` using the wrapper scripts:

```sh
./prod-run.sh python exporter/export.py       # uses .env.production
./test-run.sh python importer/import.py --since 2y  # uses .env.testing
```

- `.env.production` — production YNAB + Lunch Money credentials
- `.env.testing` — test Lunch Money account only (no YNAB token for now)

### Environment variables

| Variable | Used by |
|---|---|
| `YNAB_API_TOKEN` | exporter |
| `YNAB_BUDGET_ID` | exporter |
| `LUNCHMONEY_API_TOKEN` | importer |

If a required variable is missing, the tool must exit immediately with a clear error message naming the missing variable — never silently fall back or prompt.

### API details
- **YNAB**: Bearer token via `Authorization: Bearer TOKEN`. Rate limit: 200 requests/hour per token.
- **Lunch Money**: Bearer token via `Authorization: Bearer TOKEN`. Base URL: `https://api.lunchmoney.dev/v2`. Mock server for testing: `https://mock.lunchmoney.dev/v2`.

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
- Fields: `id`, `name`, `type`, `subtype`, `balance`, `currency`, `external_id`, `custom_metadata`, `exclude_from_transactions`
- `external_id`: use to store YNAB account UUID for deduplication on re-import
- `custom_metadata`: freeform JSON; use to store additional YNAB account info (e.g. `{"ynab_name": "...", "ynab_type": "..."}`) that may be useful for debugging or re-runs

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

## Migration Plan

### Phase 0: Accounts (v0.1)

**Prerequisite**: if any YNAB accounts have `direct_import_linked: true`, the user must have already set up Plaid bank sync for those accounts in Lunch Money before running the importer. The importer will warn and require confirmation if it cannot find a Plaid account match for a `direct_import_linked` YNAB account.

1. Export all YNAB accounts
2. Match against existing Lunch Money manual accounts (by name, or by `external_id` if re-running)
3. For YNAB accounts with `direct_import_linked: true`, match to Lunch Money Plaid accounts by name (case-insensitive) — these must NOT be re-created as manual accounts; transactions into them should target the matched Plaid account if `allow_transaction_modification: true`, or be skipped if `allow_transaction_modification: false`
   - If no Plaid match is found: warn the user and skip the account (do not create a manual account). The user must resolve this manually — either by connecting Plaid sync in LM or by explicitly opting in to creating a manual account for it
4. For closed YNAB accounts (`closed: true`): create as manual accounts in Lunch Money (so their transaction history is preserved), but note they are closed in `custom_metadata`
5. Create missing manual accounts in Lunch Money, storing YNAB UUID in `external_id`
6. **Balance check**: after all transactions are imported, compare computed balance against YNAB's `cleared_balance` and `uncleared_balance`

### Phase 0: Categories (v0.2)
1. Export YNAB category groups and categories
2. Match against existing Lunch Money categories (by name, or stored metadata)
3. Create missing categories, preserving group structure
4. Store YNAB category UUID mapping for use during transaction import

### Phase 1: Transactions
1. Export all YNAB transactions (excluding deleted)
2. For each transaction:
   - Skip if it belongs to a Plaid-linked account in Lunch Money
   - Skip transfers (where `transfer_account_id` is set) — transfers will be handled as a pair to avoid double-counting
   - Convert amount: YNAB milliunits ÷ 1000, negate sign (YNAB negative → LM positive)
   - Map `payee_name`, `category_id` (via stored mapping), `account_id`
   - Store YNAB `id` in transaction `custom_metadata` as `{"ynab_id": "..."}`
   - For split transactions (has `subtransactions`): create as split in Lunch Money
3. Show summary before applying: count of new/skipped/conflicting transactions
4. Send transactions to `POST /v2/transactions` in batches of **500** (the documented maximum per request)
5. For each batch response, process `skipped_duplicates`:
   - Each entry contains `reason`, `request_transactions_index`, `existing_transaction_id`, and `request_transaction`
   - Log each skipped transaction with its reason; count toward the pre-apply summary
   - Partial insertion is safe: non-duplicates in the same batch are inserted even when some are skipped

#### Transfer handling
YNAB creates two linked transactions for every transfer (one per account). Lunch Money may handle transfers differently. Strategy TBD — options:
- Import only one leg of each transfer pair and mark the other as skipped
- Use Lunch Money's transfer concept if available in v2

### Phase 2: Budget Assignments
- Lunch Money has a budget endpoint; confirm if it supports setting budgeted amounts per category per month
- Import YNAB monthly `budgeted` values per category
- Convert milliunits to decimal

### Phase 3: Budget Goals
- Embedded in YNAB categories; check if Lunch Money supports goals on categories
- If not supported, export to a reference file for manual recreation

### Phase 4: Scheduled Transactions
- Export YNAB scheduled transactions
- Map to Lunch Money recurring items (if the API supports creation)
- Map YNAB frequency enum to Lunch Money's recurring cadence

## Balance Reconciliation Strategy

The goal is for Lunch Money account balances to match YNAB after import.

YNAB provides two balances per account:
- `cleared_balance`: sum of cleared + reconciled transactions
- `uncleared_balance`: sum of uncleared transactions
- `balance` = `cleared_balance` + `uncleared_balance`

After importing transactions:
1. Compute the sum of imported transactions per account in Lunch Money
2. Compare against YNAB's `balance`
3. If there's a discrepancy, identify cause: skipped transfers, Plaid overlap, or rounding
4. Optionally create a reconciliation/adjustment transaction to force-match balances

**Known discrepancy risks:**
- Transfer transactions (double-counted if both legs imported, zero if neither)
- Transactions in Plaid-linked accounts that were skipped
- Starting balance transactions in YNAB (each account has an initial "Starting Balance" entry)
- Milliunits rounding (should not occur if dividing exactly by 1000)
- Currency conversion (for multi-currency budgets)

## Anti-Duplication Rules

1. Always check if a Lunch Money account with matching `external_id` already exists before creating
2. Always check transaction `custom_metadata.ynab_id` before inserting (for re-runs)
3. Respect the `skipped_duplicates` response from Lunch Money POST /transactions
4. Never import into Plaid-linked Lunch Money accounts where `allow_transaction_modification: false`
5. Never import YNAB accounts that have been matched to existing Plaid accounts

## Date-Range Filtering

The importer must support a `--since DATE` option (e.g. `--since 2023-01-01` or `--since 6mo` or `--since 2y`) that restricts which data gets imported. This is the primary way to do a partial migration — e.g. "only bring in the last 2 years" — without importing the full history.

### What gets filtered by date

| Resource | Filter applied |
|---|---|
| Transactions | `date >= since` |
| Budget assignments | month `>= since` (truncated to month boundary) |
| Scheduled transactions | `next_due_date >= since` (future-facing; always import if active) |
| Accounts | Never filtered — always imported regardless of `--since` |
| Categories | Never filtered — always imported regardless of `--since` |
| Budget goals | Never filtered — goals are on categories, not time-bound |

### Balance reconciliation under partial import

When importing with `--since`, account balances in Lunch Money will **not** match YNAB's current balance because pre-cutoff transactions are absent. To handle this:

1. For each account, compute the YNAB balance as of the cutoff date (sum of all transactions before `since`)
2. Create an **opening balance transaction** in Lunch Money dated one day before `since`, with that amount, in a designated "Opening Balance" or "Adjustments" category
3. After importing all post-cutoff transactions, verify that Lunch Money balance = YNAB current balance
4. The opening balance transaction should be stored with `custom_metadata: {"ynab_opening_balance": true, "as_of": "YYYY-MM-DD"}` so it can be identified and updated on re-runs

This ensures balances always reconcile regardless of the chosen cutoff date.

### Interaction with deduplication

The `--since` filter is applied **after** deduplication checks. If a transaction already exists in Lunch Money (by `custom_metadata.ynab_id`) but falls before the cutoff, it is still skipped — the filter only prevents *new* imports of old data, not removal of already-imported data.

## Planned Tools (Post-Migration)

Reference: `data/docs/discord-thread-about-transfers-etc.txt` — LM Discord support thread covering how users handle transfers and the lack of automatic transaction matching in LM. Key takeaways:
- LM does **not** auto-match/merge transactions from different sources (Plaid vs manual, Plaid vs 3rd-party). They remain separate entries. Clara (LM support) confirmed there is no API for linking them either (as of May 2026).
- The recommended transfer workflow is: categorize both legs to "Payment, Transfer" (which is exclude-from-budget + exclude-from-totals), then optionally group them.
- "Create transfer" from an imported transaction creates a *new* third transaction — wrong if the other leg is already imported. Do not use that UI for already-imported transfers.

### Transaction Matching Tool
After import, the user may have manually entered transactions in LM that duplicate ones coming in via Plaid sync (or YNAB-imported ones). This tool helps identify and safely resolve duplicates.

**Candidate signals — use in combination, never alone:**
1. **YNAB memo / transfer account ID**: YNAB sometimes records the transfer counterpart account ID in `transfer_account_id`. If the memo or custom_metadata on the imported transaction references the other account, that is a strong signal.
2. **Plaid-provided fields**: Plaid transactions carry a `pending` flag, a `pending_transaction_id` (links pending→posted), and sometimes a `transaction_id` that matches across sources. If both candidates share a Plaid transaction ID or pending link, that is a near-certain match.
3. **Amount + date proximity**: Same absolute amount on the same account, dates within ±3 days. Necessary but not sufficient on its own — high false-positive risk for common round amounts.
4. **Payee similarity**: Normalized payee name overlap (after stripping emoji/punctuation). Boosts confidence but not reliable alone.
5. **Account match**: Both transactions must be on the same LM account. A match across different accounts is almost certainly wrong.

**False-positive prevention:**
- Never auto-merge without explicit user confirmation per pair.
- Require at least two independent signals before surfacing a candidate (e.g. amount+date is not enough by itself; add payee or a Plaid field).
- Show the full transaction details of both sides before asking the user to confirm.
- If the user declines a pair, record it as "rejected" in local state so it is never re-surfaced.

**Why grouping alone doesn't work for deduplication:** the group parent's `amount` is the *sum* of all children. Grouping two ×$50 transactions yields a $100 group — double-counting. LM has no merge/replace API (confirmed by LM support as of May 2026).

**Grouping workaround:** one possible workaround is to zero out the manual transaction's amount and keep the real amount only on the Plaid transaction, then group both — the group total then equals the Plaid amount. This avoids double-counting without deleting either leg, at the cost of a zero-amount orphan entry.

**Resolution (preferred):** delete the manual transaction and store its full JSON in `custom_metadata.matched_manual` on the surviving (Plaid/imported) transaction. This gives a complete audit trail and allows manual recovery if the match turns out to be wrong. Note: `custom_metadata` is capped at 4096 characters when stringified; a typical transaction object is ~500–800 chars so this should fit comfortably.

**Feature tracking:** native auto-linking of manually created transactions to synced ones is tracked upstream at https://feedback.lunchmoney.app/transactions/p/auto-link-manually-created-transactions-to-synced-transactions — if that ships, the matching tool may become unnecessary.

### Transfer Management Tool
Transfers in YNAB create two linked transaction legs (one per account). In LM the recommended approach (per LM support) is:
1. Categorize both legs to the "Payment, Transfer" category (exclude-from-budget + exclude-from-totals).
2. Optionally group them using the LM transaction group API to keep them visually linked.

This tool automates that:
- **Orphan detection**: find transactions in the "Payment, Transfer" category (or with `transfer_account_id` set in YNAB metadata) that have no group partner.
- **Pair matching**: same absolute amount, opposite sign, dates within ±3 days, on accounts that are plausibly a transfer pair (both owned by user). Apply the same false-positive caution as the matching tool above.
- **Dry-run summary**: show proposed pairs and ungrouped orphans before applying anything.
- **On apply**: set category to "Payment, Transfer" on both legs if not already set, then create an LM transaction group linking the two.
- **Do not use "create transfer"** on already-imported transactions — that generates a spurious third entry.

## Multi-Budget Notes

### BRL accounts in the CAD budget
The CAD YNAB budget contains BRL accounts that were only used to represent BRL balances — they are not real CAD accounts. There is a separate BRL YNAB budget that will eventually be imported into Lunch Money independently.

**Plan**: when importing the CAD budget, these BRL accounts (and their transactions) should be excluded. The importer will need a per-account exclude/ignore mechanism — likely a flag or config that lets the user mark specific YNAB account IDs or names as excluded from a given import run.

This is low priority but the account-exclude feature should be kept in mind when designing the account import phase.

## Implementation Notes

- Importer must always print a **dry-run summary** (counts of creates/skips/conflicts per resource type) and prompt for confirmation before writing anything
- Use the Lunch Money mock server (`https://mock.lunchmoney.dev/v2`) for development and testing
- Export files in `data/` are the source of truth for the import phase — exporter and importer are independent scripts
- YNAB `deleted: true` records should be exported (for completeness) but not imported
- YNAB internal categories (e.g. "Inflow: Ready to Assign") have `internal: true` — skip during import
- Local sync state is stored in `data/<slug>/<lm_account_id>/sync_state.json` — records YNAB↔LM ID mappings for accounts, categories, and transactions, keyed by LM account ID. This allows the same YNAB budget to be imported to multiple LM accounts. Machine-generated; do not edit manually.
- LM metadata storage: accounts use `external_id` (YNAB UUID) and `custom_metadata` (YNAB type/flags); categories and transactions use `custom_metadata` only (no `external_id` field available)
